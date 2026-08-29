"""
Local test server for the website generator.

Runs the REAL generation pipeline (core/generation.py -> Gemini -> Jinja
templates) with every external connection swapped for a local stand-in.

    python local_test/server.py        # http://127.0.0.1:5000

Only GEMINI_API_KEY is required.
"""
import os
import sys
import io
import re
import uuid
import asyncio
import zipfile
import traceback
from typing import List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

# Windows: subprocess/async fix, matches app.py
if os.name == "nt":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except AttributeError:
        pass

# ---- swap the connections out BEFORE core.generation is imported ----
from local_test import shims
shims.install()

# Uvicorn installs handlers on its own loggers and leaves the root logger bare,
# so every logger.info() in core/ went nowhere — which is why the pipeline
# looked silent and why `treatment: None` in /history was mistaken for a broken
# feature rather than a missing log line. Route them to stdout.
import logging as _logging
_logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                     format="%(levelname)-7s [%(name)s] %(message)s")
_log = _logging.getLogger("server")
for _n in ("artdirector", "skills", "composition", "imagery", "imagedirector",
           "photo_pipeline", "vision", "design", "packs", "llm", "offline",
           "commerce", "payments", "publish", "notify", "server"):
    _logging.getLogger(_n).setLevel(_logging.INFO)

from fastapi import FastAPI, Request, Form, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse, Response
from config import Config
from core.generation import run_generation_job
from core.utils import clean_editor_artifacts
from core.redis import create_job_record, get_job_status, get_job_id_for_website
from core.r2 import upload_media_to_r2, fetch_media_from_r2

# The pipeline skips credit deduction for exactly this id (generation.py step 11).
DEV_USER_ID = "00000000-0000-0000-0000-000000000001"
FRONTEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend.html")

app = FastAPI(title="Website Generator - Local Test Harness")

# Load the cutout model once at boot. Left lazy it costs the first seller
# several seconds; here it costs the server a moment nobody is waiting on.
try:
    from core.imagedirector import warm as _warm_cutout
    _warm_cutout()
except Exception:
    pass

os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
os.makedirs(Config.GENERATED_FOLDER, exist_ok=True)


# Template Pack static assets (css / images / fonts / js). Generated pages
# reference these absolutely as /packs/<slug>/..., so they resolve the same
# whether the page is served from /preview/<id>/ or opened standalone.
# The storefront on a published site calls this API from another origin, so the
# API — and only the API — allows cross-origin requests. The preview and the
# merchant dashboard are same-origin and unaffected.
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in os.getenv("SHOP_ALLOWED_ORIGINS", "*").split(",") if o],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Merchant-Key"],
)

from fastapi.staticfiles import StaticFiles
_PACKS_DIR = os.path.join(ROOT, "templates", "packs")
if os.path.isdir(_PACKS_DIR):
    app.mount("/packs", StaticFiles(directory=_PACKS_DIR), name="packs")


@app.get("/", response_class=HTMLResponse)
async def index():
    with open(FRONTEND, "r", encoding="utf-8") as fh:
        return HTMLResponse(fh.read())


@app.get("/packs-info")
async def packs_info():
    """What Packs exist and what each is for — used by the UI and for debugging."""
    from core.packs import PACKS
    return {"packs": [
        {"slug": s, "title": p["title"], "character": p["character"],
         "accent": p["accent"], "source": p["source"], "sections": p["sections"],
         "mode": p.get("mode", "light"), "use_case": p.get("use_case", "")}
        for s, p in PACKS.items()]}



@app.get("/templates", response_class=HTMLResponse)
async def templates_gallery():
    """
    Browse the designs before uploading anything.

    Each is rendered live rather than screenshotted, so the gallery can never
    drift out of date the way a folder of images does.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "templates_gallery.html")
    with open(path, encoding="utf-8") as fh:
        return HTMLResponse(fh.read())


@app.get("/templates/{slug}", response_class=HTMLResponse)
async def template_preview(slug: str):
    """One design, rendered with a demo seller of the kind it was built for."""
    from core import demo
    try:
        return HTMLResponse(await asyncio.to_thread(demo.render, slug))
    except KeyError:
        raise HTTPException(status_code=404, detail="No such design")
    except Exception as e:
        _log.warning(f"template preview {slug} failed: {e}")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@app.get("/health")
async def health():
    # "key present" and "API actually works" are different questions — report
    # the second, so the UI never claims a live model when calls are failing.
    from core.llm import api_dead, provider_info
    from core import artdirector as _ad, skills as _skills
    from core.offline import api_available
    _dead, why = api_dead()
    info = provider_info()
    return {
        "status": "ok",
        "mode": "local-test",
        "provider": info["provider"],
        "vision": info["vision"],
        "free_tier": info["free_tier"],
        "key_env": info["key_env"],
        "key_present": info["key_present"],
        "ai_ready": api_available(),
        "offline": not api_available(),
        "offline_reason": why or (f"{info['key_env']} is not set"
                                  if not info["key_present"] else ""),
        "model_content": info["model_content"],
        "model_fast": info["model_fast"],
        "jobs": len(shims.JOBS),
        "websites": len(shims.WEBSITES),
        # Whether the Art Director agent can actually run, and on what. Both
        # halves can fail independently: no vision model means no critique even
        # with a live key, and no browser means nothing to critique.
        "art_director": {
            "enabled": _ad.ENABLED,
            "can_direct": bool(_ad.ENABLED and _skills.load("art-direction")
                               and api_available()),
            "can_critique": bool(_ad.ENABLED and _skills.load("page-critique")
                                 and api_available() and info["vision"]
                                 and _ad.find_chrome()),
            "browser": os.path.basename(_ad.find_chrome()) or None,
            "skills": _skills.available(),
        },
    }


# ---------------------------------------------------------------------------
# PHOTO-FIRST FLOW  (the PRD's pipeline)
# ---------------------------------------------------------------------------

@app.post("/triage")
async def triage(photo: UploadFile = File(...),
                 photos: List[UploadFile] = File([])):
    """
    FR-2 / FR-3 / FR-5 — verdict + coaching + Product Spec, BEFORE generating.

    The local quality pass is pure PIL and returns in well under 2s; the
    detection call is what the rest of the wait is.
    """
    from core.vision import intake
    tmp = os.path.join(Config.UPLOAD_FOLDER, f"{uuid.uuid4()}_{os.path.basename(photo.filename)}")
    with open(tmp, "wb") as fh:
        fh.write(await photo.read())
    try:
        from core.offline import api_available, offline_spec
        from core.vision import analyze_quality, build_guidance
        result = None
        if api_available():
            result = await intake(tmp)
            # The first failing call is what trips the breaker, so that call
            # still comes back empty — fall through to offline rather than
            # reporting a photo problem the seller cannot fix.
            if not result or not result.get("spec"):
                result = None
        if result is None:
            # Offline: measure the pixels, and ask the seller for the one
            # thing we cannot measure (what the product is).
            q = analyze_quality(tmp)
            defects = q.get("defects", []) if q.get("ok") else ["unreadable"]
            result = {
                "verdict": "fail" if not q.get("ok") else ("warn" if defects else "pass"),
                "quality": q, "defects": defects,
                "guidance": build_guidance(defects, None),
                "spec": offline_spec(tmp, q),
                "offline": True,
            }
            from core.llm import api_dead
            _dead, _why = api_dead()
            if _why:
                result["offline_reason"] = _why
        result["photo_token"] = os.path.basename(tmp)

        # Additional photos are kept and quality-checked, but detection only
        # ever runs on the first — the Product Spec describes one product.
        extra = []
        for f in (photos or []):
            if not (f and f.filename):
                continue
            ep = os.path.join(Config.UPLOAD_FOLDER,
                              f"{uuid.uuid4()}_{os.path.basename(f.filename)}")
            with open(ep, "wb") as fh:
                fh.write(await f.read())
            eq = analyze_quality(ep)
            extra.append({"token": os.path.basename(ep),
                          "long_edge": eq.get("long_edge"),
                          "defects": eq.get("defects", [])})
        result["extra_photos"] = extra
        result["photo_count"] = 1 + len(extra)
        return result
    except Exception as e:
        traceback.print_exc()
        try:
            os.remove(tmp)
        except Exception:
            pass
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/generate-from-photo")
async def generate_from_photo(
    background_tasks: BackgroundTasks,
    photo_token: str = Form(...),
    extra_tokens: str = Form(""),
    brand_name: str = Form(""),
    price: str = Form(""),
    seller_facts: str = Form(""),
    density: str = Form("generous"),
    spin: int = Form(0),
    override_triage: bool = Form(False),
    category: str = Form(""),
    sub_type: str = Form(""),
):
    """Runs the photo-first pipeline on a photo already cleared by /triage."""
    from core.photo_pipeline import run_photo_generation_job
    # No key check — the pipeline falls back to offline mode (core/offline.py)
    # and still produces a real, editable, downloadable site.

    src = os.path.join(Config.UPLOAD_FOLDER, os.path.basename(photo_token))
    if not os.path.exists(src):
        return JSONResponse(status_code=400,
                            content={"error": "photo_token expired — re-upload the photo"})

    website_id = str(uuid.uuid4())
    # The pipeline deletes its copy when done; keep the original so Spin can
    # regenerate from the same photo without a re-upload.
    work = os.path.join(Config.UPLOAD_FOLDER, f"{website_id}_work{os.path.splitext(src)[1]}")
    with open(src, "rb") as a, open(work, "wb") as b:
        payload = a.read()
        b.write(payload)

    image_url = await asyncio.to_thread(
        upload_media_to_r2, payload, "image/jpeg", f"websites/{website_id}/assets")

    # The additional photos ride along as working copies; the pipeline deletes
    # them with the first one when it is done.
    extra_paths = []
    for tok in [t.strip() for t in (extra_tokens or "").split(",") if t.strip()]:
        src_e = os.path.join(Config.UPLOAD_FOLDER, os.path.basename(tok))
        if not os.path.exists(src_e):
            continue
        dst_e = os.path.join(Config.UPLOAD_FOLDER,
                             f"{website_id}_x{len(extra_paths)}{os.path.splitext(src_e)[1]}")
        with open(src_e, "rb") as a, open(dst_e, "wb") as b:
            b.write(a.read())
        extra_paths.append(dst_e)

    job_id = str(uuid.uuid4())
    await create_job_record(job_id=job_id, website_id=website_id)
    background_tasks.add_task(
        run_photo_generation_job,
        job_id=job_id, website_id=website_id, user_id=DEV_USER_ID,
        image_path=work, image_url=image_url, extra_paths=extra_paths,
        brand_name=brand_name,
        price=price, seller_facts=seller_facts, density=density,
        spin=int(spin), override_triage=bool(override_triage),
        user_category=category, user_sub_type=sub_type,
    )
    return {"success": True, "job_id": job_id, "website_id": website_id,
            "status": "queued", "photo_token": photo_token}


@app.post("/generate")
async def generate(
    background_tasks: BackgroundTasks,
    prompt: str = Form(...),
    business_name: str = Form(""),
    industry: str = Form(""),
    pages: str = Form(""),
    palette: str = Form("auto"),
    template: str = Form("auto"),
    logo: Optional[UploadFile] = File(None),
    images: List[UploadFile] = File([]),
):
    if not Config.GEMINI_API_KEY:
        return JSONResponse(status_code=500, content={
            "error": "GEMINI_API_KEY is not set. Put it in .env and restart."})
    try:
        website_id = str(uuid.uuid4())

        logo_url = None
        if logo and logo.filename:
            logo_url = await asyncio.to_thread(
                upload_media_to_r2, await logo.read(),
                logo.content_type or "image/png",
                f"websites/{website_id}/assets")

        image_urls, image_paths = [], []
        for f in images[:Config.MAX_IMAGES]:
            if not (f and f.filename):
                continue
            data = await f.read()
            # Gemini reads pixels off local disk; the pipeline deletes these.
            local = os.path.join(Config.UPLOAD_FOLDER,
                                 f"{uuid.uuid4()}_{os.path.basename(f.filename)}")
            with open(local, "wb") as fh:
                fh.write(data)
            image_paths.append(local)
            image_urls.append(await asyncio.to_thread(
                upload_media_to_r2, data, f.content_type or "image/jpeg",
                f"websites/{website_id}/assets"))

        job_id = str(uuid.uuid4())
        await create_job_record(job_id=job_id, website_id=website_id)

        background_tasks.add_task(
            run_generation_job,
            job_id=job_id, website_id=website_id, user_id=DEV_USER_ID,
            prompt=prompt, business_name=business_name,
            image_urls=image_urls, image_paths=image_paths, logo_url=logo_url,
            user_pages=pages, user_palette=palette, user_template=template,
            user_industry=industry, db_image_records=[],
        )
        return {"success": True, "job_id": job_id,
                "website_id": website_id, "status": "queued"}
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/job-status/{job_id}")
async def job_status(job_id: str):
    data = await get_job_status(job_id)
    if not data:
        raise HTTPException(status_code=404, detail="Job not found")
    return data


@app.get("/media/{object_key:path}")
async def media(object_key: str):
    try:
        body = await asyncio.to_thread(fetch_media_from_r2, object_key)
    except Exception:
        raise HTTPException(status_code=404, detail="Not found")
    ext = object_key.rsplit(".", 1)[-1].lower()
    ctype = {"html": "text/html", "png": "image/png", "jpg": "image/jpeg",
             "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif",
             "svg": "image/svg+xml", "ico": "image/x-icon"}.get(
                 ext, "application/octet-stream")
    return Response(content=body, media_type=ctype)


@app.get("/preview/{website_id}/{filename:path}")
async def preview(website_id: str, filename: str = "home.html", edit: int = 0):
    name = (filename or "home.html").strip("/") or "home.html"
    html_bytes = None
    candidates = [f"websites/{website_id}/{name}"]
    if name in ("home.html", "index.html"):
        alt = "index.html" if name == "home.html" else "home.html"
        candidates.append(f"websites/{website_id}/{alt}")
    for key in candidates:
        try:
            html_bytes = await asyncio.to_thread(fetch_media_from_r2, key)
            if html_bytes:
                break
        except Exception:
            pass

    if not html_bytes:
        job_id = await get_job_id_for_website(website_id)
        if job_id:
            st = await get_job_status(job_id)
            if st and st.get("status") == "failed":
                return HTMLResponse(
                    "<pre>Generation failed:\n\n" + str(st.get("error")) + "</pre>")
        return HTMLResponse("<p>Still generating - refresh shortly.</p>")

    raw = html_bytes.decode("utf-8")
    # In edit mode the page is served as saved (artifacts intact); in view mode
    # the editor's own attributes are stripped so the seller sees the real site.
    html = raw if edit else clean_editor_artifacts(raw)

    # Rewrite inter-page links so nav works under the /preview prefix.
    base = "/preview/" + website_id
    for p in ("home", "about", "services", "portfolio", "contact"):
        pattern = r'href=(["\'])(?:\./|/)?' + p + r'(?:\.html)?(#[^"\']*)?\1'
        replacement = r'href=\g<1>' + base + "/" + p + r'.html\g<2>\g<1>'
        html = re.sub(pattern, replacement, html, flags=re.I)

    if edit:
        from core.editlayer import inject
        html = inject(html, website_id, name)
    return HTMLResponse(html)


@app.get("/download/{website_id}")
async def download(website_id: str, single: int = 0):
    """
    Default: a zip holding ONE self-contained index.html plus the seller's
    original photo. ?single=1 returns just the .html document on its own.
    """
    from core.r2 import list_objects_in_folder
    from core.bundle import build_single_html, build_download_zip

    keys = await asyncio.to_thread(list_objects_in_folder, f"websites/{website_id}")
    pages = {k.rsplit("/", 1)[-1]: k for k in keys if k.endswith(".html")
             and "backup" not in k}
    if not pages:
        raise HTTPException(status_code=404, detail="Nothing generated yet")

    doc = shims.WEBSITES.get(website_id, {})
    # Falls back to a Pack that actually exists. This used to name
    # "illustrator", which was removed on 27 Aug — a record missing its `pack`
    # would then inline no stylesheet at all and download as unstyled HTML.
    from core.packs import PACKS, DEFAULT_PACK
    pack_slug = doc.get("pack") or DEFAULT_PACK
    if pack_slug not in PACKS:
        pack_slug = DEFAULT_PACK
    site_name = doc.get("site_name", "website")
    packs_root = os.path.join(ROOT, "templates", "packs")

    # The seller's own uploaded photo(s), keyed by the URL the pages reference.
    home_key = pages.get("home.html") or sorted(pages.values())[0]
    home_html = (await asyncio.to_thread(fetch_media_from_r2, home_key)).decode("utf-8")
    product_images = {}
    for k in keys:
        if "/assets/" not in k or k.endswith(".html"):
            continue
        url = f"{shims.PUBLIC_URL}/{k}"
        if url in home_html:
            product_images[url] = await asyncio.to_thread(fetch_media_from_r2, k)

    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", site_name).strip("-") or "website"

    # Flatten every page into its own self-contained document. Links between
    # them are rewritten to plain filenames, so the folder works offline and
    # the nav keeps working after download.
    docs = {}
    for name, key in sorted(pages.items()):
        raw = (await asyncio.to_thread(fetch_media_from_r2, key)).decode("utf-8")
        imgs = {u: b for u, b in product_images.items() if u in raw}
        out_name = "index.html" if name == "home.html" else name
        docs[out_name] = build_single_html(raw, pack_slug, packs_root, imgs,
                                           editable=True)

    single_html = docs.get("index.html") or next(iter(docs.values()))

    if single:
        return Response(
            content=single_html.encode("utf-8"), media_type="text/html",
            headers={"Content-Disposition": f'attachment; filename="{safe}.html"'})

    blob = build_download_zip(docs, product_images, site_name)
    return Response(
        content=blob, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe}.zip"'})


@app.post("/replace-image")
async def replace_image(website_id: str = Form(...), image: UploadFile = File(...)):
    """Swap the seller's photo from inside edit mode."""
    data = await image.read()
    ext = os.path.splitext(image.filename or "")[1] or ".jpg"
    url = await asyncio.to_thread(
        upload_media_to_r2, data, image.content_type or "image/jpeg",
        f"websites/{website_id}/assets", f"{uuid.uuid4().hex}{ext}")
    return {"success": True, "url": url}


@app.post("/save")
async def save(request: Request):
    data = await request.json()
    website_id, html = data.get("website_id"), data.get("html")
    if not website_id or not html:
        raise HTTPException(status_code=400, detail="Missing website_id or html")
    page = os.path.basename(data.get("page_name") or "home.html")
    await asyncio.to_thread(upload_media_to_r2, html.encode("utf-8"),
                            "text/html", f"websites/{website_id}", page)
    return {"success": True}


@app.get("/history")
async def history():
    items = []
    for wid, doc in reversed(list(shims.WEBSITES.items())):
        items.append({
            "website_id": wid,
            "site_name": doc.get("site_name"),
            "pack": doc.get("pack"),
            "genre": doc.get("genre"),
            "spin": doc.get("spin"),
            "industry": doc.get("industry"),
            "layout": doc.get("layout", []),
            "created_at": doc.get("created_at"),
            "preview_url": f"/preview/{wid}/home.html",
            "download_url": f"/download/{wid}",
        })
    return {"success": True, "items": items}



# ══════════════════════════════════════════════════════════════════════════════
# COMMERCE
#
# The buyer-facing endpoints are public by necessity — a storefront nobody can
# reach sells nothing — so they are the ones that need protecting:
#
#   * the order endpoint is rate limited, because a public POST that writes to
#     disk is a spam target
#   * nothing the browser sends about money is trusted; core/commerce prices
#     every order from the stored catalogue
#
# The merchant endpoints change order state, so they sit behind a key. It is
# OFF by default so local testing is frictionless, and the /health payload says
# so plainly — a shop taking real orders must set SHOP_REQUIRE_KEY=1.
# ══════════════════════════════════════════════════════════════════════════════

from core import commerce as _shop
from core import payments as _pay

# Two limits, because they defend against different things and charging both
# to one counter punishes the wrong person. A buyer who mistypes their phone
# number three times is not an attacker, but the first version counted their
# failed attempts and locked them out of their own checkout.
#
#   ORDERS    successful creates — these write to disk, so they are the ones
#             worth rationing tightly
#   ATTEMPTS  every request including rejects — a looser ceiling that still
#             stops someone simply flooding the endpoint
_RATE: dict = {"orders": {}, "attempts": {}}
_RATE_MAX = int(os.getenv("SHOP_RATE_MAX", "12"))
_ATTEMPT_MAX = int(os.getenv("SHOP_ATTEMPT_MAX", "60"))
_RATE_WINDOW = int(os.getenv("SHOP_RATE_WINDOW", "300"))  # seconds


def _rate_ok(bucket: str, key: str, cap: int, record: bool = True) -> bool:
    import time as _t
    now = _t.time()
    hits = [t for t in _RATE[bucket].get(key, []) if now - t < _RATE_WINDOW]
    ok = len(hits) < cap
    if ok and record:
        hits.append(now)
    _RATE[bucket][key] = hits
    return ok


def _merchant_ok(website_id: str, request: Request) -> bool:
    """
    Guard for endpoints that change an order.

    Disabled unless SHOP_REQUIRE_KEY=1 so the local harness stays easy to drive.
    When enabled, the key is the seller's own, stored with their settings.
    """
    if os.getenv("SHOP_REQUIRE_KEY", "0") not in ("1", "true", "True"):
        return True
    want = (_shop.get_settings(website_id) or {}).get("merchant_key", "")
    got = request.headers.get("x-merchant-key", "")
    return bool(want) and got == want



@app.get("/shop/{website_id}", response_class=HTMLResponse)
async def shop_dashboard(website_id: str):
    """The merchant's order desk. Served as a plain page; it talks to the API."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
    with open(path, encoding="utf-8") as fh:
        return HTMLResponse(fh.read())


@app.get("/api/{website_id}/shop")
async def shop_info(website_id: str):
    """Everything the storefront needs to render a cart and a checkout."""
    cat = _shop.get_catalogue(website_id)
    settings = _shop.get_settings(website_id)
    items = []
    for i in cat.get("items", []):
        items.append({**i, "price_display": _shop.format_money(
            i.get("price_minor"), i.get("currency", cat.get("currency", "INR")))})
    return {"orderable": bool(cat.get("orderable")),
            "currency": cat.get("currency", "INR"),
            "items": items,
            "methods": _pay.methods_for(settings)}


@app.post("/api/{website_id}/quote")
async def shop_quote(website_id: str, request: Request):
    """
    Price a cart without placing an order.

    The cart page calls this instead of adding its numbers up locally, so what
    the buyer is shown is the same figure the server will charge.
    """
    body = await request.json()
    try:
        q = _shop.price_cart(website_id, body.get("lines") or [])
    except _shop.CommerceError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    q["total_display"] = _shop.format_money(q["total_minor"], q["currency"])
    for ln in q["lines"]:
        ln["amount_display"] = _shop.format_money(ln["amount_minor"], q["currency"])
        ln["unit_display"] = _shop.format_money(ln["unit_minor"], q["currency"])
    return q


@app.post("/api/{website_id}/orders")
async def place_order(website_id: str, request: Request):
    """Place an order. Public, rate limited, server-priced."""
    client = (request.client.host if request.client else "?") + ":" + website_id
    if not _rate_ok("attempts", client, _ATTEMPT_MAX):
        return JSONResponse(status_code=429, content={
            "error": "too many requests from here just now — please wait a moment"})
    # Checked but NOT recorded yet: only an order that is actually created
    # should spend this budget, so a rejected form does not lock the buyer out.
    if not _rate_ok("orders", client, _RATE_MAX, record=False):
        return JSONResponse(status_code=429, content={
            "error": "too many orders from here just now — please wait a moment"})

    body = await request.json()
    try:
        order = _shop.create_order(
            website_id,
            lines=body.get("lines") or [],
            customer=body.get("customer") or {},
            payment_method=str(body.get("payment_method", "cod")),
            idempotency_key=str(body.get("idempotency_key", ""))[:64],
        )
        note = _pay.instructions(_shop.get_settings(website_id), order)
    except (_shop.CommerceError, _pay.PaymentError) as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    _rate_ok("orders", client, _RATE_MAX)   # the order stuck — now count it

    # Tell the seller. Best-effort and on its own thread: the order is already
    # durable, and a webhook that is down must not turn a completed checkout
    # into an error for the buyer.
    try:
        from core import notify as _notify
        _notify.order_placed(order, _shop.get_settings(website_id),
                             (shims.WEBSITES.get(website_id, {}) or {}).get("site_name", ""))
    except Exception as e:
        _log.warning(f"order notification skipped: {e}")

    return {"success": True, "order": {
        "id": order["id"], "ref": order["ref"],
        "total_display": _shop.format_money(order["total_minor"], order["currency"]),
        "status": order["status"], "payment_status": order["payment_status"],
    }, "payment": note}


@app.post("/api/{website_id}/orders/{order_id}/paid")
async def claim_paid(website_id: str, order_id: str, request: Request):
    """
    The buyer says they have paid and gives a reference.

    Moves the order to `pending`, never `paid` — a UPI deep link tells us
    nothing, so only the seller checking their bank can settle it.
    """
    body = await request.json()
    try:
        o = _shop.claim_payment(website_id, order_id, body.get("reference", ""))
    except (_shop.CommerceError, _pay.PaymentError) as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return {"success": True, "payment_status": o["payment_status"]}


@app.get("/api/{website_id}/orders")
async def merchant_orders(website_id: str, request: Request, status: str = ""):
    if not _merchant_ok(website_id, request):
        raise HTTPException(status_code=401, detail="merchant key required")
    orders = _shop.list_orders(website_id, status)
    for o in orders:
        o["total_display"] = _shop.format_money(o["total_minor"], o["currency"])
    return {"orders": orders, "summary": _summary_display(website_id)}


def _summary_display(website_id: str) -> dict:
    s = _shop.summary(website_id)
    s["revenue_display"] = _shop.format_money(s["revenue_minor"], s["currency"])
    s["committed_display"] = _shop.format_money(s["committed_minor"], s["currency"])
    return s


@app.patch("/api/{website_id}/orders/{order_id}")
async def update_order(website_id: str, order_id: str, request: Request):
    if not _merchant_ok(website_id, request):
        raise HTTPException(status_code=401, detail="merchant key required")
    body = await request.json()
    try:
        if body.get("status"):
            o = _shop.set_status(website_id, order_id, body["status"])
        elif body.get("payment_status"):
            o = _shop.set_payment(website_id, order_id, body["payment_status"],
                                  body.get("payment_reference", ""))
        else:
            return JSONResponse(status_code=400,
                                content={"error": "nothing to change"})
    except _shop.CommerceError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return {"success": True, "order": o}



@app.post("/api/{website_id}/price")
async def set_item_price(website_id: str, request: Request):
    """Set what the product costs. Turns an enquiry-only site into a shop."""
    if not _merchant_ok(website_id, request):
        raise HTTPException(status_code=401, detail="merchant key required")
    body = await request.json()
    try:
        cat = _shop.set_price(website_id, body.get("price", ""),
                              body.get("item_id", "p1"))
    except _shop.CommerceError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    item = (cat.get("items") or [{}])[0]
    return {"success": True, "orderable": cat.get("orderable"),
            "price_display": _shop.format_money(item.get("price_minor"),
                                                cat.get("currency", "INR"))}

@app.post("/api/{website_id}/settings")
async def shop_settings(website_id: str, request: Request):
    """Seller settings — UPI ID, payee name, where to send new-order alerts."""
    if not _merchant_ok(website_id, request):
        raise HTTPException(status_code=401, detail="merchant key required")
    body = await request.json()
    if body.get("upi_vpa"):
        try:
            _pay.normalise_vpa(body["upi_vpa"])
        except _pay.PaymentError as e:
            return JSONResponse(status_code=400, content={"error": str(e)})
    return {"success": True, "settings": _shop.save_settings(website_id, body)}



# ══════════════════════════════════════════════════════════════════════════════
# PUBLISHING
#
# Doc §3: the platform handles hosting so the seller never touches a server.
# The `local` target needs no account and no token, so a site can be live the
# moment it is generated.
# ══════════════════════════════════════════════════════════════════════════════

from core import publish as _pub


@app.post("/api/{website_id}/publish")
async def publish_site(website_id: str, request: Request):
    if not _merchant_ok(website_id, request):
        raise HTTPException(status_code=401, detail="merchant key required")

    from core.r2 import list_objects_in_folder
    keys = await asyncio.to_thread(list_objects_in_folder, f"websites/{website_id}")
    pages = {}
    for k in keys:
        name = k.rsplit("/", 1)[-1]
        if not name.endswith(".html") or "backup" in k:
            continue
        raw = await asyncio.to_thread(fetch_media_from_r2, k)
        pages[name] = raw.decode("utf-8", "replace")
    if not pages:
        return JSONResponse(status_code=404,
                            content={"error": "nothing generated for this site yet"})

    doc = shims.WEBSITES.get(website_id, {}) or {}
    # Settings first: they are on disk, so they survive the restart that empties
    # the in-memory website document.
    name = ((_shop.get_settings(website_id) or {}).get("site_name")
            or doc.get("site_name") or "shop")
    try:
        rec = await asyncio.to_thread(_pub.publish_local, website_id, name, pages)
    except _pub.PublishError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return {"success": True, **rec}


@app.get("/api/{website_id}/publish")
async def publish_status(website_id: str):
    slug = _pub._slug_of(website_id)
    if not slug:
        return {"published": False, "vercel_available": _pub.vercel_available()}
    rec = _pub.get_published(slug)
    rec["published"] = True
    rec["url"] = f"/s/{slug}/"
    rec["vercel_available"] = _pub.vercel_available()
    return rec


@app.delete("/api/{website_id}/publish")
async def unpublish_site(website_id: str, request: Request):
    if not _merchant_ok(website_id, request):
        raise HTTPException(status_code=401, detail="merchant key required")
    slug = _pub._slug_of(website_id)
    return {"success": bool(slug and _pub.unpublish(slug))}


@app.get("/s/{slug}", response_class=HTMLResponse)
@app.get("/s/{slug}/", response_class=HTMLResponse)
async def published_home(slug: str):
    return await published_page(slug, "home.html")


@app.get("/s/{slug}/{filename}", response_class=HTMLResponse)
async def published_page(slug: str, filename: str):
    """
    Serve a published page. This is the buyer-facing URL — the one that goes on
    a WhatsApp status or a card — so it is deliberately short and readable.
    """
    try:
        return HTMLResponse(_pub.read_page(slug, filename))
    except _pub.PublishError:
        raise HTTPException(status_code=404, detail="Not found")
    except OSError:
        raise HTTPException(status_code=404, detail="Not found")


@app.get("/published")
async def published_index():
    return {"sites": _pub.list_published()}


if __name__ == "__main__":
    import uvicorn
    print("\n  Local test harness -> http://127.0.0.1:5000\n")
    uvicorn.run(app, host="127.0.0.1", port=5000, log_level="info")
