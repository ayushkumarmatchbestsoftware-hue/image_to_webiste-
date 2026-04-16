import os
import asyncio
import json
import uuid
from datetime import datetime
from urllib.parse import urlparse
from typing import Optional
from redis import Redis as SyncRedis  # Standard sync client
from redis.asyncio import Redis as AsyncRedis  # Async client for worker
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# Environment variables
# ─────────────────────────────────────────────

REDIS_URL = os.getenv("REDIS_URL")
JOB_TTL   = int(os.getenv("REDIS_JOB_TTL", 7200))  # 2 hours default

def _parse_redis_url(url: str) -> dict:
    parsed = urlparse(url)
    return {
        "host":     parsed.hostname or "localhost",
        "port":     parsed.port or 6379,
        "password": parsed.password or None,
        "db":       int(parsed.path.lstrip("/")) if parsed.path and parsed.path != "/" else 0,
    }

if REDIS_URL:
    _conn = _parse_redis_url(REDIS_URL)
else:
    _conn = {
        "host":     os.getenv("REDIS_IP", "localhost"),
        "port":     int(os.getenv("REDIS_PORT", 6379)),
        "password": os.getenv("REDIS_PASSWORD") or None,
        "db":       int(os.getenv("REDIS_DB", 0)),
    }

# ─────────────────────────────────────────────
# Redis clients
# ─────────────────────────────────────────────

# Standard sync client: used by app.py (fallback/polling) to avoid event loop issues.
sync_redis = SyncRedis(
    host=_conn["host"],
    port=_conn["port"],
    password=_conn["password"],
    db=_conn["db"],
    decode_responses=True,
    socket_timeout=5,
    socket_connect_timeout=5
)

# Async client: ONLY for worker.py's blocking pop (BRPOP).
# This is created at module level but will be used in the worker's single loop.
async_redis = AsyncRedis(
    host=_conn["host"],
    port=_conn["port"],
    password=_conn["password"],
    db=_conn["db"],
    decode_responses=True
)

# Backwards compatibility: point 'redis' to our async client (for worker usage)
# but for app.py routes, we will use the sync client via to_thread.
redis = async_redis

# ─────────────────────────────────────────────
# Queue names
# ─────────────────────────────────────────────

WEBSITE_AI_QUEUE = "queue:website_ai"

def _job_key(job_id: str) -> str:
    return f"job:{job_id}"

def _website_job_key(website_id: str) -> str:
    return f"website_job:{website_id}"

# ─────────────────────────────────────────────
# Robust Public API
# ─────────────────────────────────────────────

def _set_job_status_internal(job_id: str, status: str, result: Optional[dict] = None, error: Optional[str] = None):
    """Sync internal operation for thread-safe state marking."""
    payload = {
        "status": status,
        "updated_at": datetime.utcnow().isoformat() + "Z"
    }
    if error:
        payload["error"] = error
    if result:
        payload.update({k: str(v) for k, v in result.items()})
    
    key = _job_key(job_id)
    sync_redis.hset(key, mapping=payload)
    sync_redis.expire(key, JOB_TTL)

async def enqueue_website_ai_job(*, website_id: str, user_id: str, prompt: str, image_urls: list, image_paths: list, logo_url: Optional[str], user_pages: str, user_palette: str, user_industry: str, db_image_records: list) -> str:
    job_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat() + "Z"
    job_payload = {
        "job_id": job_id, "type": "WEBSITE_GENERATION", "website_id": website_id,
        "user_id": user_id, "prompt": prompt, "image_urls": image_urls,
        "image_paths": image_paths, "logo_url": logo_url, "user_pages": user_pages,
        "user_palette": user_palette, "user_industry": user_industry,
        "db_image_records": db_image_records
    }
    
    def _run():
        sync_redis.lpush(WEBSITE_AI_QUEUE, json.dumps(job_payload))
        _set_job_status_internal(job_id, "queued", result={"created_at": created_at})
        sync_redis.set(_website_job_key(website_id), job_id, ex=JOB_TTL)
        
    await asyncio.to_thread(_run)
    return job_id

async def get_job_status(job_id: str) -> Optional[dict]:
    """Helper used by app.py — now uses sync client in thread to avoid Event Loop errors."""
    def run_sync():
        data = sync_redis.hgetall(_job_key(job_id))
        return data if data else None
    
    return await asyncio.to_thread(run_sync)

async def get_job_id_for_website(website_id: str) -> Optional[str]:
    """Return the latest queued job id for a website, if Redis still has it."""
    def run_sync():
        return sync_redis.get(_website_job_key(website_id))

    return await asyncio.to_thread(run_sync)

async def mark_job_processing(job_id: str):
    await asyncio.to_thread(_set_job_status_internal, job_id, "processing")

async def mark_job_completed(job_id: str, website_id: str, preview_url: str):
    def run_sync():
        payload = {
            "status": "completed",
            "website_id": website_id,
            "preview_url": preview_url,
            "updated_at": datetime.utcnow().isoformat() + "Z"
        }
        sync_redis.hset(_job_key(job_id), mapping=payload)
        sync_redis.expire(_job_key(job_id), JOB_TTL)
        sync_redis.set(_website_job_key(website_id), job_id, ex=JOB_TTL)
    await asyncio.to_thread(run_sync)

async def mark_job_failed(job_id: str, error: str):
    def run_sync():
        payload = {
            "status": "failed",
            "error": error,
            "updated_at": datetime.utcnow().isoformat() + "Z"
        }
        sync_redis.hset(_job_key(job_id), mapping=payload)
        sync_redis.expire(_job_key(job_id), JOB_TTL)
    await asyncio.to_thread(run_sync)
