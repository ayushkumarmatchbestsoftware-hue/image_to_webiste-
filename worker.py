"""
worker.py — Website AI Generation Worker
═════════════════════════════════════════════════════════════════════════════
Runs as a SEPARATE process alongside app.py.
Pops jobs from the Redis queue (queue:website_ai) and executes the full
website generation pipeline: AI content → HTML rendering → R2 upload → DB save.

How it works:
  • Listens (BLPOP) on the Redis queue with a 5-second timeout.
  • When a job arrives it marks itself as 'processing', runs the full pipeline,
    then marks 'completed' or 'failed'.
  • All errors are caught, logged, and the job is marked 'failed' so the
    frontend can show the user a proper message.
  • Runs forever until you press Ctrl+C.

Run alongside app.py:
  python worker.py
═════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import json
import os

# Fix for Windows IOCP Error 22 on forceful restart
if os.name == 'nt':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
import sys
import traceback
import uuid as pyuuid_module
import uuid
from datetime import datetime

# Ensure worker logs can safely print Unicode on Windows consoles.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Path setup so we can import from the same project ──
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

# ── Project imports ──
from core.redis import (
    redis,
    async_redis,
    WEBSITE_AI_QUEUE,
    mark_job_processing,
    mark_job_completed,
    mark_job_failed,
)
from core.r2 import upload_media_to_r2, R2_PUBLIC_URL
from core.db import get_engine

# Flask rendering
from flask import render_template
from app import (
    app,
    generate_website_content,
    build_image_map,
    PALETTE_MAP,
    WEBSITE_CONTEXTS,
    ENABLE_CHAT_EDIT,
)
from sqlalchemy import insert
import uuid as pyuuid

# ── DB models ──
from core.mongo import insert_website_data


# ─────────────────────────────────────────────────────────────────────────────
# Structured logging helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ts() -> str:
    """ISO timestamp prefix for every log line."""
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def log(level: str, job_id: str, msg: str, **extra):
    """
    Structured log line.
    Example: [2026-04-04T09:00:00.000Z] [INFO ] [job:abc123] AI generation started | images=2
    """
    tag = f"[{_ts()}] [{level:<5}] [job:{job_id[:8]}]"
    extra_str = " | ".join(f"{k}={v}" for k, v in extra.items())
    line = f"{tag} {msg}"
    if extra_str:
        line += f" | {extra_str}"
    print(line, flush=True)


def log_worker(msg: str, **extra):
    """Log a worker-level (not job-specific) message."""
    tag = f"[{_ts()}] [WORKER]"
    extra_str = " | ".join(f"{k}={v}" for k, v in extra.items())
    line = f"{tag} {msg}"
    if extra_str:
        line += f" | {extra_str}"
    print(line, flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# Core generation pipeline
# ─────────────────────────────────────────────────────────────────────────────

async def process_job(job: dict):
    """
    Runs the full website generation pipeline for a single queued job.
    All exceptions are caught and propagated to the caller for status marking.
    """
    job_id       = job["job_id"]
    website_id   = job["website_id"]
    user_id      = job.get("user_id", "00000000-0000-0000-0000-000000000001")
    prompt       = job["prompt"]
    image_urls   = job.get("image_urls", [])    # R2 URLs of uploaded images
    image_paths  = job.get("image_paths", [])   # Local disk paths for Gemini Vision
    logo_url     = job.get("logo_url")          # R2 URL of logo (or None)
    user_pages   = job.get("user_pages", "")
    user_palette = job.get("user_palette", "auto")
    user_industry = job.get("user_industry", "")
    db_image_records = job.get("db_image_records", [])

    log("INFO", job_id, "Job dequeued — starting pipeline",
        website_id=website_id[:8], images=len(image_urls), industry=user_industry or "auto")

    # ── Step 1: Mark job as processing ──
    await mark_job_processing(job_id)
    log("INFO", job_id, "Status → processing")

    # ── Step 2: Run AI content generation ──
    log("INFO", job_id, "Calling Gemini AI for content generation",
        model="gemini-flash", image_count=len(image_paths))
    
    data = await generate_website_content(
        prompt, image_paths, len(image_paths),
        industry=user_industry or None
    )

    if not data:
        raise RuntimeError("AI returned empty response — content generation failed")

    # Clean up local temp files — Gemini Vision is done, R2 copies are permanent
    for _fp in image_paths:
        try:
            os.remove(_fp)
        except Exception:
            pass

    log("INFO", job_id, "AI generation successful",
        site_name=data.get("site_info", {}).get("display_name", "?"))

    # ── Step 3: Resolve layout ──
    if user_pages:
        valid_sections = {
            "hero", "about", "services", "portfolio",
            "testimonials", "stats", "faq", "pricing", "contact"
        }
        requested = [s.strip() for s in user_pages.split(',') if s.strip() in valid_sections]
        layout = requested if requested else data.get("layout", ["hero", "about", "services", "contact"])
    else:
        layout = data.get("layout", ["hero", "about", "services", "contact"])

    log("INFO", job_id, "Layout resolved", sections=",".join(layout))

    # ── Step 4: Apply palette override ──
    theme = data.get("theme", {})
    if user_palette and user_palette != "auto" and user_palette in PALETTE_MAP:
        theme.update(PALETTE_MAP[user_palette])
        log("INFO", job_id, "Palette override applied", palette=user_palette)

    # ── Step 5: Build image map ──
    clean_image_urls = [u for u in image_urls if u]
    image_map = build_image_map(clean_image_urls, layout)
    log("INFO", job_id, "Image map built",
        hero=bool(image_map.get("hero")),
        about=bool(image_map.get("about")),
        portfolio=len(image_map.get("portfolio", [])),
        overflow=len(image_map.get("overflow", [])))

    # ── Step 6: Prepare template context ──
    site_name  = data.get("site_info", {}).get("display_name", "My Business")
    site_title = data.get("site_info", {}).get("site_title", site_name)
    tagline    = data.get("site_info", {}).get("tagline", "")
    footer     = data.get("footer", {})

    base_ctx = dict(
        site_name=site_name, site_title=site_title,
        tagline=tagline, theme=theme, footer=footer,
        layout=layout, image_map=image_map,
        image_count=len(clean_image_urls),
        has_images=(len(clean_image_urls) > 0),
        logo=logo_url,
        services_img=image_map.get("services"),
        testimonials_img=image_map.get("testimonials"),
        overflow_imgs=image_map.get("overflow", []),
    )

    # ── Step 7: Render + upload home.html ──
    log("INFO", job_id, "Rendering home.html")
    def sync_render_home():
        with app.app_context():
            return render_template(
                "home.html", **base_ctx,
                home=data.get("home", {}),
                about=data.get("about", {}),
                services=data.get("services", []),
                portfolio=data.get("portfolio", []),
                testimonials=data.get("testimonials", []),
                faq=data.get("faq", []),
                pricing=data.get("pricing", []),
                stats=data.get("stats", []),
                contact=data.get("contact", {}),
                images=clean_image_urls,
            )
    
    home_html = await asyncio.to_thread(sync_render_home)

    await asyncio.to_thread(
        upload_media_to_r2,
        home_html.encode("utf-8"), "text/html",
        folder=f"websites/{website_id}", filename="home.html"
    )
    log("INFO", job_id, "home.html uploaded to R2")

    # ── Step 8: Render + upload sub-pages ──
    page_templates = {
        "about.html":     ("about.html",     dict(**base_ctx, about=data.get("about", {}), services=data.get("services", []), images=clean_image_urls)),
        "services.html":  ("services.html",  dict(**base_ctx, services=data.get("services", []), images=clean_image_urls)),
        "portfolio.html": ("portfolio.html", dict(**base_ctx, portfolio=data.get("portfolio", []), images=clean_image_urls)),
        "contact.html":   ("contact.html",   dict(**base_ctx, contact=data.get("contact", {}), images=clean_image_urls)),
    }

    def sync_render_page(tmpl, ctx):
        with app.app_context():
            return render_template(tmpl, **ctx)

    for out_name, (tmpl, ctx) in page_templates.items():
        try:
            html = await asyncio.to_thread(sync_render_page, tmpl, ctx)
            await asyncio.to_thread(
                upload_media_to_r2,
                html.encode("utf-8"), "text/html",
                folder=f"websites/{website_id}", filename=out_name
            )
            log("INFO", job_id, f"{out_name} uploaded to R2")
        except Exception as page_err:
            log("WARN", job_id, f"Failed to render/upload {out_name}", error=str(page_err))

    # ── Step 9: Backup home.html ──
    try:
        await asyncio.to_thread(
            upload_media_to_r2,
            home_html.encode("utf-8"), "text/html",
            folder=f"websites/{website_id}", filename="home_backup.html"
        )
        log("INFO", job_id, "home_backup.html uploaded to R2")
    except Exception as bkp_err:
        log("WARN", job_id, "Backup upload failed — non-critical", error=str(bkp_err))

    # ── Step 10: Persist to database (MongoDB) ──
    log("INFO", job_id, "Persisting to database", records=len(db_image_records))
    try:
        website_doc = {
            "website_id": website_id,
            "user_id": str(user_id),
            "prompt": prompt,
            "status": "completed",
            "progress": "100",
            "final_url": f"{R2_PUBLIC_URL}/websites/{website_id}/home.html",
            "industry": user_industry,
            "site_name": site_name,
            "tagline": tagline,
            "layout": list(layout),
            "theme": dict(theme),
            "footer": dict(footer) if isinstance(footer, dict) else footer,
            "ai_data": data,
            "chat_messages": []
        }
        await insert_website_data(website_doc, db_image_records)
        log("INFO", job_id, "Database persisted to MongoDB successfully")
    except Exception as db_err:
        log("ERROR", job_id, "MongoDB persistence failed — generation still succeeded", error=str(db_err))

    # ── Step 11: Store in-memory context for chat-edit (if enabled) ──
    if ENABLE_CHAT_EDIT:
        WEBSITE_CONTEXTS[website_id] = {
            "prompt":        prompt,
            "industry":      user_industry,
            "layout":        list(layout),
            "theme":         dict(theme),
            "data":          data,
            "image_context": clean_image_urls,
            "image_map":     dict(image_map),
            "logo":          logo_url,
            "site_name":     site_name,
            "site_title":    site_title,
            "tagline":       tagline,
            "footer":        footer,
        }
        log("INFO", job_id, "In-memory context stored for chat-edit")
    # ── Step 12: Credit Deduction ──
    if str(user_id) != '00000000-0000-0000-0000-000000000001':
        try:
            from handlers.credit_handler import website_credits_debits
            cost = int(os.getenv("WEBSITE_AI_CREDIT_COST", 1))
            credit_res = await website_credits_debits({
                "userId": user_id,
                "resourceType": "website_generation",
                "resourceId": website_id,
                "type": "USAGE",
                "amount": -cost,
                "description": f"Website Generation (job {job_id})"
            })
            if not credit_res.get("success"):
                log("ERROR", job_id, "Failed to deduct credits", error=credit_res.get("error"))
            else:
                log("INFO", job_id, f"Credits deducted successfully (Cost: {cost})")
        except Exception as credit_err:
            log("ERROR", job_id, "Credit deduction threw exception", error=str(credit_err))

    # ── Step 13: Mark job completed ──
    preview_url = f"/preview/{website_id}/home.html"
    await mark_job_completed(job_id, website_id, preview_url)
    log("INFO", job_id, "✓ Job COMPLETED", preview_url=preview_url)

    return website_id, preview_url


# ─────────────────────────────────────────────────────────────────────────────
# Worker event loop
# ─────────────────────────────────────────────────────────────────────────────

async def run_worker():
    """
    Main worker loop.
    Blocks on BRPOP with a 5-second timeout so it never burns CPU while idle.
    Processes one job at a time (safe and predictable).
    """
    log_worker("=" * 60)
    log_worker("Website AI Worker STARTED")
    log_worker(f"Listening on queue: {WEBSITE_AI_QUEUE}")
    log_worker("=" * 60)

    # Ping Redis to confirm connection before entering loop
    try:
        from core.redis import redis as redis_client
        pong = await redis_client.ping()
        log_worker("Redis connection: OK", pong=pong)
    except Exception as conn_err:
        log_worker("FATAL: Cannot connect to Redis — exiting", error=str(conn_err))
        sys.exit(1)

    while True:
        try:
            # BRPOP blocks up to 5 seconds waiting for a job
            # Returns: (queue_name, json_payload) or None on timeout
            result = await redis.brpop(WEBSITE_AI_QUEUE, timeout=5)

            if result is None:
                # Timeout — no jobs in queue, loop again silently
                continue

            _, raw = result
            log_worker("Job received from queue — deserializing")

            try:
                job = json.loads(raw)
            except json.JSONDecodeError as je:
                log_worker("ERROR: Failed to deserialize job payload — skipping", error=str(je))
                continue

            job_id    = job.get("job_id", "unknown")
            job_type  = job.get("type", "unknown")
            log("INFO", job_id, f"Job type={job_type}")

            if job_type != "WEBSITE_GENERATION":
                log("WARN", job_id, f"Unknown job type '{job_type}' — skipping")
                continue

            # ── Process the job ──
            try:
                log("INFO", job_id, "Starting job processing...")
                # Wrap with timeout to prevent hung jobs (25 min timeout)
                try:
                    await asyncio.wait_for(process_job(job), timeout=1500)
                    log("INFO", job_id, "Job processing completed successfully")
                except asyncio.TimeoutError:
                    error_msg = "Worker timeout: Job processing took longer than 25 minutes"
                    log("ERROR", job_id, error_msg)
                    print(f">>> [WORKER TIMEOUT] {job_id}: {error_msg}\n", flush=True)
                    try:
                        await mark_job_failed(job_id, error_msg)
                    except Exception as redis_err:
                        log("ERROR", job_id, f"Could not mark timeout job as failed: {str(redis_err)}")
            except Exception as job_err:
                error_msg = f"{type(job_err).__name__}: {str(job_err)}"
                log("ERROR", job_id, "Job FAILED", error=error_msg)
                print(f"\n>>> [WORKER ERR] Traceback for {job_id}:", flush=True)
                traceback.print_exc()
                print(f">>> [WORKER ERR] End traceback\n", flush=True)
                try:
                    log("INFO", job_id, "Attempting to mark job as failed in Redis...")
                    await mark_job_failed(job_id, error_msg)
                    log("INFO", job_id, "Successfully marked job as failed")
                except Exception as redis_err:
                    error_mark_msg = f"Failed to mark job failed: {type(redis_err).__name__}: {str(redis_err)}"
                    log("ERROR", job_id, error_mark_msg)
                    print(f">>> [WORKER CRITICAL] {error_mark_msg}", flush=True)
                    traceback.print_exc()

        except KeyboardInterrupt:
            log_worker("Worker received shutdown signal (Ctrl+C) — exiting cleanly")
            break
        except Exception as loop_err:
            # Catch any unexpected error in the loop itself to prevent crash
            log_worker("UNEXPECTED loop error — recovering", error=str(loop_err))
            print(f"\n>>> [WORKER LOOP ERR] Unexpected error in main loop:\n", flush=True)
            traceback.print_exc()
            print(f">>> [WORKER LOOP ERR] End traceback\n", flush=True)
            await asyncio.sleep(2)  # Brief pause before retrying

    # Cleanup
    try:
        await async_redis.close()
    except Exception:
        pass
    log_worker("Worker STOPPED")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    asyncio.run(run_worker())
