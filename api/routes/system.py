"""Index, health, and what designs are installed."""
import asyncio
import io
import os
import re
import uuid
import zipfile

from fastapi import (APIRouter, Request, Form, File, UploadFile, HTTPException,
                     BackgroundTasks)
from fastapi.responses import JSONResponse, HTMLResponse, Response, RedirectResponse

from api import ROOT
from core import sites as _sites
from core import storage as _storage
from core import jobs as _jobs
from api.deps import log, DEV_USER_ID
from config import Config
from core.storage import save as store_save, load as store_load

router = APIRouter(tags=["System & Diagnostics"])


@router.get("/")
async def index():
    """Service root endpoint."""
    return {"status": "ok", "service": "Image to Website API", "version": "1.0.0"}


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
    from core import bgremover as _bg
    from core import i18n as _i18n
    from core.offline import api_available
    _dead, why = api_dead()
    info = provider_info()
    return {
        "status": "ok",
        # "local-test" described a mode that no longer exists: the service
        # used to swap its storage and job store for stand-ins, and this said
        # which half was running. There is one implementation now, so this
        # names where the site data actually is.
        "mode": "file-store",
        "store": _storage.STORE_DIR,
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
        "jobs": len(_jobs.JOBS),
        "websites": len(_sites.ids()),
        # Whether the Art Director agent can actually run, and on what. Both
        # halves can fail independently: no vision model means no critique even
        # with a live key, and no browser means nothing to critique.
        # Imagery follows the text provider unless IMAGE_PROVIDER overrides it,
        # so a key swap is visible here in one place.
        "background_replace": _bg.info(),
        # Cached languages. Any other code still works — it is
        # translated once on first use and cached beside these.
        "languages": _i18n.available(),
        # What a seller may actually choose. `languages` above is only what is
        # already cached; this is the offered set, and the interface builds its
        # list from it so the two can never disagree.
        "languages_offered": [{"code": c, "name": n} for c, n in _i18n.offered()],
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
