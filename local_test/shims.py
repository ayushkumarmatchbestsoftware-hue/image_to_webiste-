"""
Local stand-ins for every external connection this project makes.

Import and install BEFORE anything imports core.generation:

    from local_test import shims
    shims.install()
    from core.generation import run_generation_job   # now bound to the fakes

Replaces
  core.redis              -> in-process dicts (job state)
  core.r2                 -> ./local_store on disk (object storage)
  core.mongo              -> in-process dict (website documents)
  services.vercel_service -> no-op stub

Nothing in the tracked repo is modified; the real modules are simply never
imported, so boto3 / motor / a live Redis are all unnecessary.
"""
import os
import sys
import json
import uuid
import types
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE_DIR = os.path.join(ROOT, "local_store")

# Where generated pages/assets are served from. Templates bake this into
# <img src>, so it must be an address the browser can actually reach.
# Where generated pages point for their images.
#
# This used to be a hardcoded http://127.0.0.1:5000/media, which is only right
# if the server happens to be on port 5000. Launch it on any other port — as
# every run in this project does — and every product image on every preview is
# a broken icon, while the file sits on disk perfectly intact.
#
# Relative is the correct default: it resolves against whatever host and port
# is actually serving the page. PUBLIC_BASE_URL makes it absolute again, which
# real hosting needs anyway so that og:image works when a link is shared.
_BASE = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
PUBLIC_URL = os.getenv("LOCAL_PUBLIC_URL") or (f"{_BASE}/media" if _BASE else "/media")

# Live state, inspectable from the server process.
JOBS = {}            # job_id -> status dict
WEBSITE_TO_JOB = {}  # website_id -> job_id
WEBSITES = {}        # website_id -> website document
NOTIFICATIONS = []   # payloads that would have gone to the notification queue


def _now():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- R2 (files)
def _r2_module():
    m = types.ModuleType("core.r2")
    m.R2_PUBLIC_URL = PUBLIC_URL
    m.R2_BUCKET_NAME = "local-store"

    def _path_for(object_key):
        return os.path.join(STORE_DIR, *object_key.split("/"))

    def upload_media_to_r2(file_bytes, content_type, folder="websites",
                           filename=None, file_extension=None):
        if filename:
            object_key = f"{folder}/{filename}"
        else:
            ext = file_extension or content_type.split("/")[-1]
            object_key = f"{folder}/{uuid.uuid4()}.{ext}"
        dest = _path_for(object_key)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(file_bytes)
        print(f"[STORE] wrote {object_key} ({len(file_bytes)} bytes)")
        return f"{PUBLIC_URL}/{object_key}"

    def fetch_media_from_r2(object_key):
        src = _path_for(object_key)
        if not os.path.exists(src):
            raise FileNotFoundError(object_key)
        with open(src, "rb") as fh:
            return fh.read()

    def list_objects_in_folder(prefix):
        base = _path_for(prefix)
        out = []
        for dirpath, _dirs, files in os.walk(base):
            for f in files:
                full = os.path.join(dirpath, f)
                out.append(os.path.relpath(full, STORE_DIR).replace(os.sep, "/"))
        return out

    m.upload_media_to_r2 = upload_media_to_r2
    m.fetch_media_from_r2 = fetch_media_from_r2
    m.list_objects_in_folder = list_objects_in_folder
    m.get_r2_client = lambda: None
    return m


# ------------------------------------------------------------- Redis (jobs)
def _redis_module():
    m = types.ModuleType("core.redis")

    async def create_job_record(*, job_id, website_id):
        JOBS[job_id] = {"status": "queued", "website_id": website_id,
                        "created_at": _now(), "error": None, "url": None,
                        "progress": 0, "stage": "Getting ready", "detail": ""}
        WEBSITE_TO_JOB[website_id] = job_id
        print(f"[JOB] {job_id[:8]} queued (website {website_id[:8]})")

    async def get_job_status(job_id):
        return JOBS.get(job_id)

    async def get_job_id_for_website(website_id):
        return WEBSITE_TO_JOB.get(website_id)

    async def set_job_progress(job_id, percent, label, detail=""):
        j = JOBS.setdefault(job_id, {})
        # Never let the bar run backwards: a late update from a slower stage
        # would otherwise make it jump back and read as a stall.
        if percent >= j.get("progress", 0):
            j.update(progress=percent, stage=label, detail=detail, updated_at=_now())

    async def mark_job_processing(job_id):
        JOBS.setdefault(job_id, {}).update(status="processing", updated_at=_now())
        print(f"[JOB] {job_id[:8]} processing")

    async def mark_job_completed(job_id, website_id, preview_url,
                                 notification_payload=None):
        JOBS.setdefault(job_id, {}).update(
            status="completed", website_id=website_id, url=preview_url,
            progress=100, stage="Ready", updated_at=_now())
        print(f"[JOB] {job_id[:8]} COMPLETED -> {preview_url}")

    async def mark_job_failed(job_id, error):
        JOBS.setdefault(job_id, {}).update(
            status="failed", error=str(error), updated_at=_now())
        print(f"[JOB] {job_id[:8]} FAILED: {error}")

    async def enqueue_notification(payload):
        NOTIFICATIONS.append(payload)

    def close_all_sync_clients():
        pass

    for fn in (create_job_record, get_job_status, get_job_id_for_website,
               mark_job_processing, mark_job_completed, mark_job_failed,
               enqueue_notification, set_job_progress):
        setattr(m, fn.__name__, fn)
    m.close_all_sync_clients = close_all_sync_clients
    return m


# ----------------------------------------------------------- Mongo (records)
def _mongo_module():
    m = types.ModuleType("core.mongo")

    async def insert_website_data(website_data, images_data=None):
        wid = website_data.get("website_id")
        WEBSITES[wid] = dict(website_data)
        WEBSITES[wid]["created_at"] = _now()
        print(f"[DOC] stored website {wid[:8]} '{website_data.get('site_name')}'")
        return wid

    async def update_website_final_url(website_id, final_url):
        WEBSITES.setdefault(website_id, {})["final_url"] = final_url

    async def get_website_layout(website_id):
        return WEBSITES.get(website_id, {}).get("layout", [])

    async def update_website_layout(website_id, new_layout):
        WEBSITES.setdefault(website_id, {})["layout"] = new_layout

    async def insert_chat_message(website_id, role, content):
        WEBSITES.setdefault(website_id, {}).setdefault("chat_messages", []).append(
            {"role": role, "content": content, "at": _now()})

    def get_websites_collection():
        raise RuntimeError("Mongo collection API is not available in local mode")

    for fn in (insert_website_data, update_website_final_url,
               get_website_layout, update_website_layout, insert_chat_message):
        setattr(m, fn.__name__, fn)
    m.get_websites_collection = get_websites_collection
    m.get_mongo_client = lambda: None
    m.get_mongo_db = lambda: None
    return m


# ------------------------------------------------------------------- Vercel
def _vercel_module():
    m = types.ModuleType("services.vercel_service")

    async def run_vercel_deployment(*args, **kwargs):
        raise RuntimeError("Vercel deployment is disabled in local test mode")

    m.run_vercel_deployment = run_vercel_deployment
    return m


def install():
    """Register the fakes so later `from core.x import y` resolves to them."""
    os.makedirs(STORE_DIR, exist_ok=True)
    sys.modules["core.r2"] = _r2_module()
    sys.modules["core.redis"] = _redis_module()
    sys.modules["core.mongo"] = _mongo_module()
    sys.modules["services.vercel_service"] = _vercel_module()
    print(f"[SHIM] external connections replaced (store: {STORE_DIR})")
