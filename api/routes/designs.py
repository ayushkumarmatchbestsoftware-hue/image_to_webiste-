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
from api.deps import log, UI_DIR, FRONTEND, DEV_USER_ID
from config import Config
from core.storage import save as store_save, load as store_load

router = APIRouter(tags=["Design Packs"])


@router.get("/templates", response_class=HTMLResponse)
async def templates_gallery():
    """
    Browse the designs before uploading anything.

    Each is rendered live rather than screenshotted, so the gallery can never
    drift out of date the way a folder of images does.
    """
    path = os.path.join(UI_DIR, "designs.html")
    with open(path, encoding="utf-8") as fh:
        return HTMLResponse(fh.read())


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
