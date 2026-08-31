"""
Real generation progress.

A timer that fills a bar over an assumed duration is a lie the moment a stage
runs long — and the two model calls in this pipeline vary between 3s and 20s,
so an assumed duration is always wrong. These percentages come from the job
itself: each stage reports when it starts, and the weights below are set from
measured timings, so the bar tracks work actually done.

The weights are the share of wall-clock each stage took across the runs
measured so far. Copy and detection dominate; everything else is rounding.
"""
import logging

logger = logging.getLogger("progress")

# stage -> (percent at which this stage BEGINS, label shown to the seller)
STAGES = {
    "queued":     (0,   "Getting ready"),
    "triage":     (6,   "Checking your photo"),
    "detect":     (12,  "Working out what it is"),
    "design":     (34,  "Choosing colours and layout"),
    "copy":       (40,  "Writing your words"),
    "imagery":    (48,  "Preparing your pictures"),
    "copy_done":  (74,  "Words ready"),
    "render":     (86,  "Building the pages"),
    "storing":    (94,  "Saving"),
    "done":       (100, "Ready"),
}


async def report(job_id: str, stage: str, detail: str = "") -> None:
    """
    Record which stage a job has reached. Never raises — a progress update
    failing must not be able to fail a generation.
    """
    pct, label = STAGES.get(stage, (None, stage))
    if pct is None:
        return
    try:
        from core.jobs import set_job_progress
        await set_job_progress(job_id, pct, label, detail)
    except Exception as e:
        # Never raises. A progress update that fails must not be able to
        # fail a generation - the bar stalls, the site still ships.
        logger.debug(f"progress {stage} not recorded: {e}")
