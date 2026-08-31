"""
The web layer.

Importing this package reads .env and configures logging, in that order,
before any route imports a core module that reads settings at import time.

There used to be a step between them that replaced core.r2, core.redis and
core.mongo with local stand-ins. There is nothing left to replace: storage and
job state ARE local now, so the single implementation is the one that runs
everywhere and there is no swap to get wrong.
"""
import asyncio
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

# Windows needs the Proactor loop for subprocesses (the Art Director shells out
# to a browser). Matches app.py.
if os.name == "nt":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except AttributeError:
        pass

# Uvicorn installs handlers on its own loggers and leaves the root logger bare,
# so every logger.info() in core/ went nowhere. That is why the pipeline looked
# silent, and why a missing log line was once mistaken for a broken feature.
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(levelname)-7s [%(name)s] %(message)s")
for _n in ("artdirector", "skills", "composition", "imagery", "imagedirector",
           "photo_pipeline", "vision", "design", "packs", "llm", "offline",
           "commerce", "payments", "publish", "notify", "server"):
    logging.getLogger(_n).setLevel(logging.INFO)
