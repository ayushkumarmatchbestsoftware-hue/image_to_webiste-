"""Index, health, and what designs are installed."""
import asyncio
import io
import os
import re
import uuid
import zipfile

from fastapi import (APIRouter, Request, Form, File, UploadFile, HTTPException,
                     BackgroundTasks)
from fastapi.responses import JSONResponse, HTMLResponse, Response

from api import ROOT, local_mode
from api.deps import log, UI_DIR, FRONTEND, DEV_USER_ID
from config import Config
from core.r2 import upload_media_to_r2, fetch_media_from_r2

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index():
    with open(FRONTEND, "r", encoding="utf-8") as fh:
        return HTMLResponse(fh.read())


@router.get("/packs-info")
async def packs_info():
    """What Packs exist and what each is for — used by the UI and for debugging."""
    from core.packs import PACKS
    return {"packs": [
        {"slug": s, "title": p["title"], "character": p["character"],
         "accent": p["accent"], "source": p["source"], "sections": p["sections"],
         "mode": p.get("mode", "light"), "use_case": p.get("use_case", "")}
        for s, p in PACKS.items()]}
@router.get("/health")
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
        "jobs": len(local_mode.JOBS),
        "websites": len(local_mode.WEBSITES),
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
