"""
core/generation.py — Website generation & deployment pipeline
═════════════════════════════════════════════════════════════════════════════
Runs IN-PROCESS inside app.py's own uvicorn process, scheduled via FastAPI's
BackgroundTasks (see app.py's /generate and /deploy routes). There is no
separate worker process and no Redis job queue anymore — this module is the
direct replacement for the old worker.py, ported near-verbatim.

Two public entry points, both designed to be passed straight to
`background_tasks.add_task(...)`:
    run_generation_job(...)   — AI content → HTML rendering → R2 upload → DB save
    run_deployment_job(...)   — package already-generated HTML → deploy to Vercel

Concurrency safety: since app.py runs as a single uvicorn worker process
(--workers 1), unrestricted BackgroundTasks could let unlimited concurrent
requests all run Gemini calls + PIL image decoding in that one process at
once. Two module-level asyncio.Semaphores (one for generation, one for
deployment — different resource profiles) cap how many of each run
concurrently, standing in for the old single-worker-process seriality without
fully re-serializing everything.
═════════════════════════════════════════════════════════════════════════════
"""

import asyncio
import html
import json
import os
import traceback
from collections import OrderedDict
from datetime import datetime

from core.telemetry import (
    extract_trace_context,
    get_log_correlation_fields,
    get_tracer,
    json_logs_enabled,
)

# NOTE: configure_logging() / setup_telemetry() are already called once by
# app.py at process import time. Calling them again here would duplicate
# logging handlers / telemetry exporters — do not call them in this module.
tracer = get_tracer(__name__)

# ── Project imports ──
from core.redis import (
    mark_job_processing,
    mark_job_completed,
    mark_job_failed,
    enqueue_notification,
)
from core.r2 import upload_media_to_r2, fetch_media_from_r2, R2_PUBLIC_URL
from core.mongo import insert_website_data, update_website_final_url
from services.vercel_service import run_vercel_deployment

# Jinja2 rendering (standalone environment — NOT app.py's Jinja2Templates).
# Starlette's Jinja2Templates defaults to autoescape=True; these site
# templates are authored assuming raw/unescaped interpolation (only one
# `| safe` filter exists across all of them), so a standalone Environment
# with Jinja2's own default (autoescape=False) is required to avoid silently
# HTML-escaping every AI-generated string. Mirrors worker.py's own setup.
import jinja2

template_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
jinja_env = jinja2.Environment(loader=jinja2.FileSystemLoader(template_dir))

from core.utils import (
    generate_website_content_logic,
    build_image_map_logic,
    get_fallback_tokens_logic,
    get_layout_blueprint_logic,
    validate_and_fix_theme,
)
from core.constants import (
    NICHE_DESIGN,
    LAYOUT_POOLS,
    PALETTE_MAP,
    TEMPLATE_MAP,
    INDUSTRY_TEMPLATES,
    system_prompt_text,
)

# ── Project configuration ──
ENABLE_CHAT_EDIT = os.getenv("ENABLE_CHAT_EDIT", "False").lower() == "true"

# Leak Fix (ported from worker.py): size-capped OrderedDict so in-memory
# website contexts never grow beyond MAX_CONTEXTS entries. LRU-evicted.
_WEBSITE_CONTEXTS_MAX = int(os.getenv("WEBSITE_CONTEXTS_MAX", 200))


class _BoundedDict(OrderedDict):
    """OrderedDict that evicts the oldest entry when the size cap is hit."""
    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        if len(self) > _WEBSITE_CONTEXTS_MAX:
            oldest = next(iter(self))
            del self[oldest]


WEBSITE_CONTEXTS = _BoundedDict()  # Local in-memory context (not used if chat is disabled)

# AI Client — lazily imported/constructed (see get_genai_client() below).
# `google.genai` is the single heaviest import in this codebase (~45MB RSS
# just to import it, measured) and this module is only ever touched by
# background generation/deploy jobs, never by request-handling code paths,
# so deferring the import to first actual use is a pure win here too.
from config import Config

_genai_client = None
_genai_client_init_attempted = False


def get_genai_client():
    global _genai_client, _genai_client_init_attempted
    if not _genai_client_init_attempted:
        _genai_client_init_attempted = True
        try:
            from google import genai
            _api_key = Config.GEMINI_API_KEY
            _genai_client = genai.Client(api_key=_api_key) if _api_key else None
        except Exception:
            _genai_client = None
    return _genai_client


def get_fallback_tokens(p):
    return get_fallback_tokens_logic(p, NICHE_DESIGN)


def get_layout_blueprint(p):
    return get_layout_blueprint_logic(p, LAYOUT_POOLS)


def _build_favicon_variants(logo_bytes: bytes, website_id: str) -> dict:
    """
    Resize the uploaded logo into standard favicon sizes (32x32 for the
    browser tab icon, 180x180 for apple-touch-icon), padded onto a
    transparent square canvas so non-square logos aren't distorted, and
    upload each as its own R2 asset. Returns {size: url}.

    Raises on anything PIL can't handle (e.g. an SVG logo — PIL can't
    rasterize vector formats) or any other failure. Callers must treat this
    as best-effort and fall back to the original logo URL on exception —
    a bad favicon must never fail the whole generation job.
    """
    from PIL import Image  # lazy import — only needed when a logo was uploaded
    import io

    img = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
    urls = {}
    for size in (32, 180):
        # Fit within ~86% of the canvas (not the full size) so the logo
        # always has a small margin instead of touching the edge on
        # whichever dimension its aspect ratio happens to fill exactly —
        # a logo flush against the favicon's border reads as a cropped/
        # cut-off icon rather than a deliberately-designed one.
        fit_size = max(1, round(size * 0.86))
        thumb = img.copy()
        thumb.thumbnail((fit_size, fit_size), Image.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        offset = ((size - thumb.width) // 2, (size - thumb.height) // 2)
        canvas.paste(thumb, offset, thumb)
        out = io.BytesIO()
        canvas.save(out, format="PNG")
        urls[size] = upload_media_to_r2(
            out.getvalue(), "image/png",
            folder=f"websites/{website_id}/assets", filename=f"favicon-{size}.png"
        )
    return urls


# ── Concurrency caps ──
# Env-tunable so ops can dial concurrency without a code change (still
# requires a process restart to take effect, same as any other env var read
# at import time). Two independent semaphores: generation work (Gemini call +
# PIL image decode/close) and deployment work (Vercel CLI subprocess + image
# recompression) have different resource profiles and shouldn't starve each
# other under a burst of either kind of request.
GENERATION_MAX_CONCURRENCY = int(os.getenv("GENERATION_MAX_CONCURRENCY", "3"))
DEPLOY_MAX_CONCURRENCY = int(os.getenv("DEPLOY_MAX_CONCURRENCY", "2"))
_generation_semaphore = asyncio.Semaphore(GENERATION_MAX_CONCURRENCY)
_deploy_semaphore = asyncio.Semaphore(DEPLOY_MAX_CONCURRENCY)


# ─────────────────────────────────────────────────────────────────────────────
# Structured logging helpers (ported from worker.py)
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
    extra = {**extra, **get_log_correlation_fields()}
    if json_logs_enabled():
        print(
            json.dumps(
                {
                    "timestamp": _ts(),
                    "level": level.lower(),
                    "logger": "generation",
                    "job_id": job_id,
                    "message": msg,
                    **extra,
                },
                default=str,
                ensure_ascii=False,
            ),
            flush=True,
        )
        return
    extra_str = " | ".join(f"{k}={v}" for k, v in extra.items())
    line = f"{tag} {msg}"
    if extra_str:
        line += f" | {extra_str}"
    print(line, flush=True)


def log_service(msg: str, **extra):
    """Log a service-level (not job-specific) message."""
    tag = f"[{_ts()}] [GENERATION]"
    extra = {**extra, **get_log_correlation_fields()}
    if json_logs_enabled():
        print(
            json.dumps(
                {
                    "timestamp": _ts(),
                    "level": "info",
                    "logger": "generation",
                    "message": msg,
                    **extra,
                },
                default=str,
                ensure_ascii=False,
            ),
            flush=True,
        )
        return
    extra_str = " | ".join(f"{k}={v}" for k, v in extra.items())
    line = f"{tag} {msg}"
    if extra_str:
        line += f" | {extra_str}"
    print(line, flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# Website generation pipeline
# ─────────────────────────────────────────────────────────────────────────────

async def run_generation_job(
    *,
    job_id: str,
    website_id: str,
    user_id: str,
    prompt: str,
    business_name: str = "",
    image_urls: list,
    image_paths: list,
    logo_url,
    user_pages: str,
    user_palette: str,
    user_template: str,
    user_industry: str,
    db_image_records: list,
) -> None:
    """
    Outer wrapper scheduled via background_tasks.add_task(...) from /generate.
    Bounds worst-case duration to 25 minutes (matches the old worker's cap)
    and guarantees the job is always marked failed on any unexpected error —
    a background task's exceptions are otherwise silently swallowed by
    FastAPI/Starlette, so this is the only place that can report failure.
    """
    parent_context = extract_trace_context(None)
    span_ctx = (
        tracer.start_as_current_span(
            "website_generator.generation.job",
            context=parent_context,
            attributes={"job.id": job_id, "job.type": "WEBSITE_GENERATION", "website.id": website_id},
        )
        if tracer
        else None
    )
    if span_ctx:
        span_ctx.__enter__()
    try:
        try:
            await asyncio.wait_for(
                _run_generation_job_inner(
                    job_id=job_id, website_id=website_id, user_id=user_id, prompt=prompt,
                    business_name=business_name,
                    image_urls=image_urls, image_paths=image_paths, logo_url=logo_url,
                    user_pages=user_pages, user_palette=user_palette, user_template=user_template,
                    user_industry=user_industry, db_image_records=db_image_records,
                ),
                timeout=1500,
            )
            log("INFO", job_id, "Job completed")
        except asyncio.TimeoutError:
            error_msg = "Job processing took longer than 25 minutes"
            log("ERROR", job_id, f"[GENERATION FAILED] {error_msg}")
            await mark_job_failed(job_id, error_msg)
        except Exception as job_err:
            error_msg = f"{type(job_err).__name__}: {str(job_err)}"
            log("ERROR", job_id, "[GENERATION FAILED]", error=error_msg)
            traceback.print_exc()
            try:
                await mark_job_failed(job_id, error_msg)
            except Exception:
                pass
    finally:
        if span_ctx:
            span_ctx.__exit__(None, None, None)


async def _run_generation_job_inner(
    *,
    job_id: str,
    website_id: str,
    user_id: str,
    prompt: str,
    business_name: str = "",
    image_urls: list,
    image_paths: list,
    logo_url,
    user_pages: str,
    user_palette: str,
    user_template: str,
    user_industry: str,
    db_image_records: list,
):
    """
    Runs the full website generation pipeline for a single job. All
    exceptions propagate to run_generation_job() for status marking.
    """
    async with _generation_semaphore:
        # Status flips to "processing" only once a concurrency slot is
        # actually acquired — it stays "queued" while waiting on the
        # semaphore, preserving the old status semantic (worker.py marked
        # "processing" the moment a single worker process picked the job up).
        await mark_job_processing(job_id)
        log("INFO", job_id, "Status → processing",
            website_id=website_id[:8], images=len(image_urls), industry=user_industry or "auto")

        # ── Step 1: Run AI content generation ──
        log("INFO", job_id, "Calling Gemini AI for content generation",
            model="gemini-flash", image_count=len(image_paths))

        try:
            data = await generate_website_content_logic(
                get_genai_client(),
                prompt,
                system_prompt_text,
                get_fallback_tokens,
                get_layout_blueprint,
                INDUSTRY_TEMPLATES,
                validate_and_fix_theme,
                image_paths,
                len(image_paths),
                industry=user_industry or None,
            )
        finally:
            # Always clean up local temp files, regardless of whether Gemini
            # succeeded or raised — this fixes a pre-existing bug where a
            # failed generation left these files on disk forever (the old
            # worker.py only removed them on the success path, after the
            # `if not data: raise` check below).
            for _fp in image_paths:
                try:
                    os.remove(_fp)
                except Exception:
                    pass

        if not data:
            raise RuntimeError("AI returned empty response — content generation failed")

        log("INFO", job_id, "AI generation successful",
            site_name=data.get("site_info", {}).get("display_name", "?"))

        # ── Step 2: Resolve layout ──
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

        # ── Step 3: Apply template + palette overrides ──
        theme = data.get("theme", {})
        if user_template and user_template != "auto" and user_template in TEMPLATE_MAP:
            theme.update(TEMPLATE_MAP[user_template])
            log("INFO", job_id, "Template style applied", template=user_template)
        if user_palette and user_palette != "auto" and user_palette in PALETTE_MAP:
            theme.update(PALETTE_MAP[user_palette])
            log("INFO", job_id, "Palette override applied", palette=user_palette)

        # ── Step 4: Build image map ──
        clean_image_urls = [u for u in image_urls if u]
        image_map = build_image_map_logic(clean_image_urls, layout)

        # ── Step 4b: Build favicon-sized logo variants (best-effort) ──
        # Never allowed to fail the job — any error here just falls back to
        # using the original, full-size logo URL as the favicon href, same
        # as before this feature existed.
        favicon_url = None
        favicon_apple_url = None
        favicon_sized = False
        if logo_url:
            try:
                object_key = (
                    logo_url[len(R2_PUBLIC_URL) + 1:]
                    if R2_PUBLIC_URL and logo_url.startswith(R2_PUBLIC_URL)
                    else None
                )
                if object_key:
                    logo_bytes = await asyncio.to_thread(fetch_media_from_r2, object_key)
                    favicon_urls = await asyncio.to_thread(_build_favicon_variants, logo_bytes, website_id)
                    favicon_url = favicon_urls.get(32)
                    favicon_apple_url = favicon_urls.get(180)
                    favicon_sized = bool(favicon_url)
                    log("INFO", job_id, "Favicon variants generated (32x32, 180x180)")
            except Exception as fav_err:
                log("WARN", job_id, "Favicon resize skipped — using original logo as favicon", error=str(fav_err))
        if not favicon_url:
            favicon_url = logo_url
        if not favicon_apple_url:
            favicon_apple_url = favicon_url

        # ── Step 5: Filter Layout & Prepare final context ──
        ai_display_name = data.get("site_info", {}).get("display_name", "My Business")
        if business_name and business_name.strip():
            # The user's literal brand name always wins over whatever the AI
            # invented for display_name — this is what shows in the browser
            # tab title next to the favicon, and throughout the nav/footer/
            # hero, so it must faithfully reflect what the user actually
            # typed rather than an AI paraphrase. Collapse stray whitespace
            # and HTML-escape it, since this is raw user input flowing into
            # templates rendered with autoescape=False.
            site_name = html.escape(" ".join(business_name.split()))
        else:
            site_name = ai_display_name
        site_title = data.get("site_info", {}).get("site_title", site_name)
        tagline = data.get("site_info", {}).get("tagline", "")
        footer = data.get("footer", {})

        # Dedicated browser-tab title — kept separate from site_title because
        # site_title is also the literal hero H1 on the homepage (home.html),
        # where the fuller AI-written phrase is exactly what's wanted there.
        # The browser tab itself is always just the brand name (the exact
        # name from the input, or the AI's display_name if none was typed) —
        # never combined with a tagline, so there's no way for it to ever
        # look duplicated regardless of what the AI writes for site_title.
        page_title = site_name

        # Only keep sections that were actually returned by the AI (key exists in data)
        # IMPORTANT: use 'section in data' NOT 'data.get(section)' because {} and [] are falsy
        original_layout = list(layout)
        active_layout = []
        for section in original_layout:
            if section == 'hero' or section in data:
                active_layout.append(section)

        log("INFO", job_id, f"Pruned layout from {len(original_layout)} to {len(active_layout)} active sections")

        base_ctx = dict(
            site_name=site_name, site_title=site_title, page_title=page_title,
            tagline=tagline, theme=theme, footer=footer,
            layout=active_layout, image_map=image_map,
            image_count=len(clean_image_urls),
            has_images=(len(clean_image_urls) > 0),
            logo=logo_url,
            favicon_url=favicon_url,
            favicon_apple_url=favicon_apple_url,
            favicon_sized=favicon_sized,
            services_img=image_map.get("services"),
            testimonials_img=image_map.get("testimonials"),
            overflow_imgs=image_map.get("overflow", []),
            images=clean_image_urls
        )

        # ── Step 6: Render + upload home.html ──
        raw_stats = data.get("stats", [])
        normalized_stats = []
        if isinstance(raw_stats, list):
            for s in raw_stats:
                if isinstance(s, dict):
                    label = s.get("label") or s.get("lbl", "")
                    number = s.get("number") or s.get("value") or s.get("val", "")
                    normalized_stats.append({"label": label, "number": number})

        home_html = jinja_env.get_template("home.html").render(
            **base_ctx,
            home=data.get("home", {}),
            about=data.get("about", {}),
            services=data.get("services", []),
            portfolio=data.get("portfolio", []),
            testimonials=data.get("testimonials", []),
            faq=data.get("faq", []),
            pricing=data.get("pricing", []),
            stats=normalized_stats,
            contact=data.get("contact", {}),
        )

        print(f"[GEN IO] -> Starting R2 Upload for website_id={website_id}")
        await asyncio.to_thread(
            upload_media_to_r2,
            home_html.encode("utf-8"), "text/html",
            folder=f"websites/{website_id}", filename="home.html"
        )
        log("INFO", job_id, "home.html uploaded to R2")

        # ── Step 7: Render + upload sub-pages ──
        page_templates_config = {
            "about.html": ("about.html", "about"),
            "services.html": ("services.html", "services"),
            "portfolio.html": ("portfolio.html", "portfolio"),
            "contact.html": ("contact.html", "contact"),
        }

        for out_name, (tmpl, section_key) in page_templates_config.items():
            if section_key in active_layout:
                try:
                    extra_ctx = {section_key: data.get(section_key, {})}
                    if section_key == 'about':
                        extra_ctx['services'] = data.get('services', [])

                    html = jinja_env.get_template(tmpl).render(
                        **base_ctx,
                        **extra_ctx
                    )
                    await asyncio.to_thread(
                        upload_media_to_r2,
                        html.encode("utf-8"), "text/html",
                        folder=f"websites/{website_id}", filename=out_name
                    )
                    log("INFO", job_id, f"{out_name} uploaded to R2")
                except Exception as page_err:
                    log("WARN", job_id, f"Failed to render/upload {out_name}", error=str(page_err))

        # ── Step 8: Backup home.html ──
        try:
            await asyncio.to_thread(
                upload_media_to_r2,
                home_html.encode("utf-8"), "text/html",
                folder=f"websites/{website_id}", filename="home_backup.html"
            )
            log("INFO", job_id, "home_backup.html uploaded to R2")
        except Exception as bkp_err:
            log("WARN", job_id, "Backup upload failed — non-critical", error=str(bkp_err))

        # ── Step 9: Persist to database (MongoDB) ──
        log("INFO", job_id, "Persisting to database", records=len(db_image_records))
        try:
            website_doc = {
                "website_id": website_id,
                "user_id": str(user_id),
                "prompt": prompt,
                "logo": logo_url,
                "status": "completed",
                "progress": "100",
                "final_url": f"{R2_PUBLIC_URL}/websites/{website_id}/home.html",
                "industry": user_industry,
                "site_name": site_name,
                "tagline": tagline,
                "layout": active_layout,
                "theme": dict(theme),
                "footer": dict(footer) if isinstance(footer, dict) else footer,
                "ai_data": data,
                "chat_messages": []
            }
            print(f"[GEN IO]    Persisting to Mongo | website_id={website_id}")
            await insert_website_data(website_doc, db_image_records)
            print("[GEN IO] <- Generation Pipeline | SUCCESS")
            log("INFO", job_id, "Database persisted to MongoDB successfully")
        except Exception as db_err:
            log("ERROR", job_id, "MongoDB persistence failed — generation still succeeded", error=str(db_err))

        # ── Step 10: Store in-memory context for chat-edit (if enabled) ──
        if ENABLE_CHAT_EDIT:
            WEBSITE_CONTEXTS[website_id] = {
                "prompt": prompt,
                "industry": user_industry,
                "layout": list(active_layout),
                "theme": dict(theme),
                "data": data,
                "image_context": clean_image_urls,
                "image_map": dict(image_map),
                "logo": logo_url,
                "site_name": site_name,
                "site_title": site_title,
                "tagline": tagline,
                "footer": footer,
            }
            log("INFO", job_id, "In-memory context stored for chat-edit")

        # ── Step 11: Credit Deduction ──
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

        # ── Step 12: Mark job completed + Send Notification ──
        preview_url = f"/preview/{website_id}/home.html"
        full_preview_url = f"{R2_PUBLIC_URL}/websites/{website_id}/home.html"

        notification_payload = {
            "type": "SEND_NOTIFICATION",
            "userId": str(user_id),
            "title": "Website Builder: Your Site is Ready! 🎨",
            "body": "Your new AI website has been generated. Tap to preview and edit.",
            "mediaUrl": logo_url,
            "mediaType": "image",
            "data": {
                "action": "open_deep_link",
                "route": "/website_preview",
                "generationId": job_id,
                "toolType": "website_builder",
                "mediaType": "image",
                "imageUrl": logo_url,
                "deep_link": full_preview_url,
                "websiteId": website_id
            },
            "priority": "high"
        }
        await enqueue_notification(notification_payload)

        await mark_job_completed(job_id, website_id, preview_url, notification_payload)
        log("INFO", job_id, "✓ Job COMPLETED", preview_url=preview_url)


# ─────────────────────────────────────────────────────────────────────────────
# Vercel deployment pipeline
# ─────────────────────────────────────────────────────────────────────────────

async def run_deployment_job(*, job_id: str, website_id: str, user_id: str) -> None:
    """
    Outer wrapper scheduled via background_tasks.add_task(...) from /deploy.
    Same timeout/error-handling shape as run_generation_job().
    """
    parent_context = extract_trace_context(None)
    span_ctx = (
        tracer.start_as_current_span(
            "website_generator.generation.deploy",
            context=parent_context,
            attributes={"job.id": job_id, "job.type": "VERCEL_DEPLOYMENT", "website.id": website_id},
        )
        if tracer
        else None
    )
    if span_ctx:
        span_ctx.__enter__()
    try:
        try:
            await asyncio.wait_for(
                _run_deployment_job_inner(job_id=job_id, website_id=website_id, user_id=user_id),
                timeout=1500,
            )
            log("INFO", job_id, "Job completed")
        except asyncio.TimeoutError:
            error_msg = "Deployment took longer than 25 minutes"
            log("ERROR", job_id, f"[DEPLOY FAILED] {error_msg}")
            await mark_job_failed(job_id, error_msg)
        except Exception as job_err:
            error_msg = f"{type(job_err).__name__}: {str(job_err)}"
            log("ERROR", job_id, "[DEPLOY FAILED]", error=error_msg)
            traceback.print_exc()
            try:
                await mark_job_failed(job_id, error_msg)
            except Exception:
                pass
    finally:
        if span_ctx:
            span_ctx.__exit__(None, None, None)


async def _run_deployment_job_inner(*, job_id: str, website_id: str, user_id: str):
    async with _deploy_semaphore:
        print(f"[GEN IO] -> Processing VERCEL_DEPLOYMENT | website_id={website_id}")
        log("INFO", job_id, f"[DEPLOY START] for {website_id}")
        await mark_job_processing(job_id)
        vercel_url = await run_vercel_deployment(website_id)
        print(f"[GEN IO]    Vercel URL captured: {vercel_url}")

        # Update the persistent database (History) with the real Vercel URL
        try:
            await update_website_final_url(website_id, vercel_url)
            log("INFO", job_id, f"Database updated with Vercel URL for {website_id}")
        except Exception as db_err:
            log("ERROR", job_id, "Failed to update final_url in DB", error=str(db_err))

        # Send Notification for Deployment Complete
        notification_payload = {
            "type": "SEND_NOTIFICATION",
            "userId": user_id,
            "title": "Website Builder: Your Site is Live! 🚀",
            "body": "Successfully deployed to Vercel production.",
            "mediaUrl": None,
            "mediaType": "image",
            "data": {
                "action": "open_deep_link",
                "route": "/website_preview",
                "generationId": job_id,
                "toolType": "website_builder",
                "mediaType": "image",
                "imageUrl": None,
                "deep_link": vercel_url,
                "websiteId": website_id
            },
            "priority": "high"
        }
        await enqueue_notification(notification_payload)

        await mark_job_completed(job_id, website_id, vercel_url, notification_payload)
        print("[GEN IO] <- VERCEL_DEPLOYMENT SUCCESS")
        log("INFO", job_id, f"[DEPLOY SUCCESS]: {vercel_url}")
