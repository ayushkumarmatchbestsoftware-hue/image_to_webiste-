"""Serving, editing and downloading a generated site."""
import asyncio
import io
import os
import re
import uuid
import zipfile

from fastapi import (APIRouter, Request, Form, File, UploadFile, HTTPException,
                     BackgroundTasks)
from fastapi.responses import JSONResponse, HTMLResponse, Response

from api import ROOT
from core import sites as _sites
from core.storage import PUBLIC_URL
from api.deps import log, UI_DIR, FRONTEND, DEV_USER_ID
from config import Config
from core.storage import save as store_save, load as store_load

from core.utils import clean_editor_artifacts
from core.jobs import get_job_status, get_job_id_for_website

router = APIRouter()


@router.get("/media/{object_key:path}")
async def media(object_key: str):
    try:
        body = await asyncio.to_thread(store_load, object_key)
    except Exception:
        raise HTTPException(status_code=404, detail="Not found")
    ext = object_key.rsplit(".", 1)[-1].lower()
    ctype = {"html": "text/html", "png": "image/png", "jpg": "image/jpeg",
             "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif",
             "svg": "image/svg+xml", "ico": "image/x-icon"}.get(
                 ext, "application/octet-stream")
    return Response(content=body, media_type=ctype)


@router.get("/preview/{website_id}/{filename:path}")
async def preview(website_id: str, filename: str = "home.html", edit: int = 0):
    name = (filename or "home.html").strip("/") or "home.html"
    html_bytes = None
    candidates = [f"websites/{website_id}/{name}"]
    if name in ("home.html", "index.html"):
        alt = "index.html" if name == "home.html" else "home.html"
        candidates.append(f"websites/{website_id}/{alt}")
    for key in candidates:
        try:
            html_bytes = await asyncio.to_thread(store_load, key)
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


@router.get("/download/{website_id}")
async def download(website_id: str, single: int = 0):
    """
    Default: a zip holding ONE self-contained index.html plus the seller's
    original photo. ?single=1 returns just the .html document on its own.
    """
    from core.storage import listing as store_listing
    from core.bundle import build_single_html, build_download_zip

    keys = await asyncio.to_thread(store_listing, f"websites/{website_id}")
    pages = {k.rsplit("/", 1)[-1]: k for k in keys if k.endswith(".html")
             and "backup" not in k}
    if not pages:
        raise HTTPException(status_code=404, detail="Nothing generated yet")

    doc = _sites.record(website_id)
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
    home_html = (await asyncio.to_thread(store_load, home_key)).decode("utf-8")
    product_images = {}
    for k in keys:
        if "/assets/" not in k or k.endswith(".html"):
            continue
        url = f"{PUBLIC_URL}/{k}"
        if url in home_html:
            product_images[url] = await asyncio.to_thread(store_load, k)

    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", site_name).strip("-") or "website"

    # Flatten every page into its own self-contained document. Links between
    # them are rewritten to plain filenames, so the folder works offline and
    # the nav keeps working after download.
    docs = {}
    for name, key in sorted(pages.items()):
        raw = (await asyncio.to_thread(store_load, key)).decode("utf-8")
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


@router.post("/replace-image")
async def replace_image(website_id: str = Form(...), image: UploadFile = File(...)):
    """Swap the seller's photo from inside edit mode."""
    data = await image.read()
    ext = os.path.splitext(image.filename or "")[1] or ".jpg"
    url = await asyncio.to_thread(
        store_save, data, image.content_type or "image/jpeg",
        f"websites/{website_id}/assets", f"{uuid.uuid4().hex}{ext}")
    return {"success": True, "url": url}


@router.post("/save")
async def save(request: Request):
    data = await request.json()
    website_id, html = data.get("website_id"), data.get("html")
    if not website_id or not html:
        raise HTTPException(status_code=400, detail="Missing website_id or html")
    page = os.path.basename(data.get("page_name") or "home.html")
    await asyncio.to_thread(store_save, html.encode("utf-8"),
                            "text/html", f"websites/{website_id}", page)
    return {"success": True}


@router.get("/history")
async def history():
    items = []
    for wid, doc in _sites.every():
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


@router.get("/api/{website_id}/design")
async def current_design(website_id: str):
    """Which design this site uses, and what it could be changed to."""
    from core.packs import PACKS
    from core import redesign
    try:
        current = (redesign.load_content(website_id) or {}).get("pack")
        changeable = True
    except redesign.RedesignError:
        current, changeable = _sites.record(website_id).get("pack"), False
    return {"current": current, "changeable": changeable,
            "designs": [{"slug": s, "title": p["title"], "mode": p.get("mode", "light"),
                         "use_case": p.get("use_case", ""),
                         "character": p.get("character", "")}
                        for s, p in PACKS.items()]}


@router.post("/api/{website_id}/design")
async def change_design(website_id: str, request: Request):
    """
    Re-render this site in a different design.

    Costs nothing and calls no model: the copy, palette and images were saved
    at generation time, so only the rendering is redone. The seller asked to
    change the look, not to be given different words.
    """
    from core import redesign
    body = await request.json()
    slug = str(body.get("pack", "")).strip()
    try:
        pages = await asyncio.to_thread(redesign.render, website_id, slug)
        await asyncio.to_thread(redesign.save, website_id, pages, slug)
    except redesign.RedesignError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        log.warning(f"redesign failed for {website_id[:8]}: {e}")
        return JSONResponse(status_code=500,
                            content={"error": f"{type(e).__name__}: {e}"})
    return {"success": True, "pack": slug, "pages": sorted(pages),
            "preview_url": f"/preview/{website_id}/home.html"}
