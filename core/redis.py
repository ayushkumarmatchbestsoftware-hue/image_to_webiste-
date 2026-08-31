import os
import asyncio
import json
from datetime import datetime
from urllib.parse import urlparse
from typing import Optional
from redis import Redis as SyncRedis  # Standard sync client
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

# Standard sync client: the only Redis client used by the app now. All job
# generation/deployment work runs in-process (see core/generation.py) via
# asyncio.to_thread, so a single sync client with a capped pool is sufficient —
# there is no separate worker process needing its own async/blocking client.
# Leak Fix #3: cap the pool at 10 connections so the client never leaks unbounded
# TCP sockets under load. socket_keepalive keeps idle sockets verified alive.
sync_redis = SyncRedis(
    host=_conn["host"],
    port=_conn["port"],
    password=_conn["password"],
    db=_conn["db"],
    decode_responses=True,
    socket_timeout=5,
    socket_connect_timeout=5,
    max_connections=10,
)

# ─────────────────────────────────────────────
# Notification Redis (dedicated client for DB 1)
# ─────────────────────────────────────────────

# Leak Fix #3: cap notification pool too.
notif_redis = SyncRedis(
    host=os.getenv("NOTIF_REDIS_HOST", "51.44.144.71"),
    port=int(os.getenv("NOTIF_REDIS_PORT", 6380)),
    password=os.getenv("NOTIF_REDIS_PASSWORD", "vcUHF8jfdfGGF016FGVG7jKF86HGC"),
    db=int(os.getenv("NOTIF_REDIS_DB", 1)),
    decode_responses=True,
    ssl=os.getenv("NOTIF_REDIS_TLS", "false").lower() == "true",
    max_connections=5,
)


# ─────────────────────────────────────────────
# Queue names
# ─────────────────────────────────────────────

NOTIFICATION_QUEUE = "notification-queue:wait" # BullMQ 'wait' list with empty prefix

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

async def create_job_record(*, job_id: str, website_id: str) -> None:
    """
    Create the initial "queued" status row for a job that will be processed
    in-process via a FastAPI BackgroundTask (see core/generation.py) — this
    replaces the old enqueue_website_ai_job/enqueue_deployment_job functions,
    minus the Redis LIST push, since there is no separate queue/worker anymore.
    Used identically by both /generate and /deploy.
    """
    created_at = datetime.utcnow().isoformat() + "Z"

    def _run():
        _set_job_status_internal(job_id, "queued", result={"created_at": created_at})
        sync_redis.set(_website_job_key(website_id), job_id, ex=JOB_TTL)

    await asyncio.to_thread(_run)

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

async def mark_job_completed(job_id: str, website_id: str, preview_url: str, notification_payload: dict = None):
    def run_sync():
        payload = {
            "status": "completed",
            "website_id": website_id,
            "preview_url": preview_url,
            "updated_at": datetime.utcnow().isoformat() + "Z"
        }
        if notification_payload:
            payload["notification"] = json.dumps(notification_payload)
            
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
