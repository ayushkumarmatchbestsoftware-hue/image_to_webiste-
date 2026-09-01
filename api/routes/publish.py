"""Putting a site on a public URL, and serving it there."""
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
from api.deps import log, DEV_USER_ID
from config import Config
from core.storage import save as store_save, load as store_load

router = APIRouter(tags=["Publishing"])
from api.deps import rate_ok, merchant_ok, summary_display
from core import publish as _pub
from core import commerce as _shop


from core import publish as _pub


@router.post("/api/{website_id}/publish")
async def publish_site(website_id: str, request: Request):
    if not merchant_ok(website_id, request):
        raise HTTPException(status_code=401, detail="merchant key required")

    from core.storage import listing as store_listing
    keys = await asyncio.to_thread(store_listing, f"websites/{website_id}")
    pages = {}
    for k in keys:
        name = k.rsplit("/", 1)[-1]
        if not name.endswith(".html") or "backup" in k:
            continue
        raw = await asyncio.to_thread(store_load, k)
        pages[name] = raw.decode("utf-8", "replace")
    if not pages:
        return JSONResponse(status_code=404,
                            content={"error": "nothing generated for this site yet"})

    doc = _sites.record(website_id)
    # Settings first: they are on disk, so they survive the restart that empties
    # the in-memory website document.
    name = ((_shop.get_settings(website_id) or {}).get("site_name")
            or doc.get("site_name") or "shop")
    try:
        rec = await asyncio.to_thread(_pub.publish_local, website_id, name, pages)
    except _pub.PublishError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    return {"success": True, **rec}


@router.get("/api/{website_id}/publish")
async def publish_status(website_id: str):
    slug = _pub._slug_of(website_id)
    if not slug:
        return {"published": False}
    rec = _pub.get_published(slug)
    rec["published"] = True
    rec["url"] = f"/s/{slug}/"
    return rec


@router.delete("/api/{website_id}/publish")
async def unpublish_site(website_id: str, request: Request):
    if not merchant_ok(website_id, request):
        raise HTTPException(status_code=401, detail="merchant key required")
    slug = _pub._slug_of(website_id)
    return {"success": bool(slug and _pub.unpublish(slug))}


@router.get("/s/{slug}", response_class=HTMLResponse)
@router.get("/s/{slug}/", response_class=HTMLResponse)
async def published_home(slug: str):
    return await published_page(slug, "home.html")


@router.get("/s/{slug}/{filename}", response_class=HTMLResponse)
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


@router.get("/published")
async def published_index():
    return {"sites": _pub.list_published()}
