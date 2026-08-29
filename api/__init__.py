"""
The web layer.

Importing this package performs the bootstrap the whole service depends on,
and the ORDER of it is load-bearing:

  1. read .env
  2. replace the external services with local stand-ins
  3. configure logging

Step 2 has to happen before anything imports core.generation, because that
module binds core.r2 / core.redis / core.mongo at import time. Doing it here
means any `from api.routes... import` gets it right without every module having
to remember, which is exactly the kind of thing that breaks a week later.
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

from api import local_mode
local_mode.install()

# Uvicorn installs handlers on its own loggers and leaves the root logger bare,
# so every logger.info() in core/ went nowhere. That is why the pipeline looked
# silent, and why a missing log line was once mistaken for a broken feature.
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(levelname)-7s [%(name)s] %(message)s")
for _n in ("artdirector", "skills", "composition", "imagery", "imagedirector",
           "photo_pipeline", "vision", "design", "packs", "llm", "offline",
           "commerce", "payments", "publish", "notify", "server"):
    logging.getLogger(_n).setLevel(logging.INFO)
