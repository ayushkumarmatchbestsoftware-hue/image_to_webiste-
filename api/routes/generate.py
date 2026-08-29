"""Photo triage and the two generation entry points."""
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

from typing import List, Optional
import traceback
from core.redis import create_job_record, get_job_status

router = APIRouter()


@router.post("/triage")
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


@router.post("/generate-from-photo")
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
    language: str = Form("en"),
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
        spin=int(spin), language=language,
        override_triage=bool(override_triage),
        user_category=category, user_sub_type=sub_type,
    )
    return {"success": True, "job_id": job_id, "website_id": website_id,
            "status": "queued", "photo_token": photo_token}



@router.get("/job-status/{job_id}")
async def job_status(job_id: str):
    data = await get_job_status(job_id)
    if not data:
        raise HTTPException(status_code=404, detail="Job not found")
    return data
