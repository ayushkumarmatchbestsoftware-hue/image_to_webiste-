"""The design gallery — every Pack rendered live with a demo seller."""
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
from api.deps import log, DEV_USER_ID
from config import Config
from core.storage import save as store_save, load as store_load

router = APIRouter(tags=["Design Packs"])


@router.get("/templates")
async def templates_gallery():
    """List all available design packs and their metadata."""
    from core.packs import PACKS
    return {"packs": [
        {"slug": s, "title": p["title"], "character": p["character"],
         "accent": p["accent"], "source": p["source"], "sections": p["sections"],
         "mode": p.get("mode", "light"), "use_case": p.get("use_case", "")}
        for s, p in PACKS.items()]}


@router.get("/templates/{slug}", response_class=HTMLResponse)
async def template_preview(slug: str):
    """One design, rendered with a demo seller of the kind it was built for."""
    from core import demo
    try:
        return HTMLResponse(await asyncio.to_thread(demo.render, slug))
    except KeyError:
        raise HTTPException(status_code=404, detail="No such design")
    except Exception as e:
        log.warning(f"template preview {slug} failed: {e}")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
