"""
What a generation is doing right now.

A generation takes up to a minute, so the request cannot wait on it. The route
starts the work, hands back a job id, and the page polls until the job says it
is finished. This holds that state.

State lives in this process, which means it is lost on restart. That is the
honest trade for having no database: a generation running when the server
restarts is gone, and the seller starts it again. If durability across restarts
or across instances is ever needed, this module is the seam to put a job queue
behind.
"""
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger("jobs")

JOBS: dict = {}            # job_id -> status
WEBSITE_TO_JOB: dict = {}  # website_id -> job_id

# How many finished jobs are kept. Nothing evicted from these dictionaries
# before, so a container that had generated ten thousand sites was still
# holding the status of all ten thousand — small records, but they only ever
# grew, and a process that never gives memory back is a process that gets
# killed eventually.
#
# A job's status is only read while the page is polling it, which stops the
# moment the site is delivered. Keeping the last few hundred covers a reload
# and a slow tab; older ones answer 404, which the page already handles as
# "that job is gone".
MAX_JOBS = int(os.getenv("MAX_JOBS_KEPT", "300"))


def _evict() -> None:
    """Drop the oldest records once the store is over its limit."""
    if len(JOBS) <= MAX_JOBS:
        return
    # created_at is an ISO timestamp, so lexical order is chronological.
    oldest = sorted(JOBS, key=lambda k: JOBS[k].get("created_at") or "")
    for job_id in oldest[:len(JOBS) - MAX_JOBS]:
        site = (JOBS.pop(job_id, {}) or {}).get("website_id")
        if site and WEBSITE_TO_JOB.get(site) == job_id:
            WEBSITE_TO_JOB.pop(site, None)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def create_job_record(*, job_id: str, website_id: str) -> None:
    JOBS[job_id] = {"status": "queued", "website_id": website_id,
                    "created_at": _now(), "error": None, "url": None,
                    "progress": 0, "stage": "Getting ready", "detail": ""}
    WEBSITE_TO_JOB[website_id] = job_id
    _evict()
    logger.info(f"{job_id[:8]} queued (website {website_id[:8]})")


async def get_job_status(job_id: str):
    return JOBS.get(job_id)


async def get_job_id_for_website(website_id: str):
    return WEBSITE_TO_JOB.get(website_id)


async def set_job_progress(job_id: str, percent: int, label: str,
                           detail: str = "") -> None:
    j = JOBS.setdefault(job_id, {})
    # Never let the bar run backwards: a late update from a slower stage would
    # otherwise make it jump back, which reads as a stall.
    if percent >= j.get("progress", 0):
        j.update(progress=percent, stage=label, detail=detail,
                 updated_at=_now())


async def mark_job_processing(job_id: str) -> None:
    JOBS.setdefault(job_id, {}).update(status="processing", updated_at=_now())
    logger.info(f"{job_id[:8]} processing")


async def mark_job_completed(job_id: str, website_id: str, preview_url: str,
                             notification_payload: dict = None) -> None:
    JOBS.setdefault(job_id, {}).update(
        status="completed", website_id=website_id, url=preview_url,
        progress=100, stage="Ready", updated_at=_now())
    logger.info(f"{job_id[:8]} completed -> {preview_url}")


async def mark_job_failed(job_id: str, error: str) -> None:
    JOBS.setdefault(job_id, {}).update(status="failed", error=str(error),
                                       updated_at=_now())
    logger.warning(f"{job_id[:8]} failed: {error}")
