"""
The application.

Everything here is assembly: create the app, apply middleware, mount static
assets, register the routers. The endpoints themselves live in api/routes/,
one module per area, because this file was 869 lines carrying generation,
site serving, commerce, publishing and the design gallery at once — and the
first thing any engineer would have said about it.

    python -m uvicorn api.server:app --reload --port 8000

Importing `api` first is what makes this work: that package's __init__ reads
.env and swaps the external services for local stand-ins BEFORE any router
imports core.generation, which binds those services at import time.
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api import ROOT                      # runs the bootstrap — keep first
from api.routes import (system, designs, generate, sites, shop, publish)
from config import Config

app = FastAPI(title="Image to Website")

# The storefront on a published site calls this API from another origin, so the
# API allows cross-origin requests. The preview and the order desk are
# same-origin and unaffected.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in os.getenv("SHOP_ALLOWED_ORIGINS", "*").split(",") if o],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Merchant-Key"],
)

os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
os.makedirs(Config.GENERATED_FOLDER, exist_ok=True)

# Pack assets (css / images / fonts). Generated pages reference these absolutely
# as /packs/<slug>/..., so they resolve the same whether the page is served from
# /preview/<id>/ or opened on its own.
_PACKS_DIR = os.path.join(ROOT, "templates", "packs")
if os.path.isdir(_PACKS_DIR):
    app.mount("/packs", StaticFiles(directory=_PACKS_DIR), name="packs")

# Load the cutout model at boot. Left lazy it costs the first seller several
# seconds; here it costs the server a moment nobody is waiting on.
try:
    from core.imagedirector import warm as _warm_cutout
    _warm_cutout()
except Exception:
    pass

# Order matters for one pair only: publish declares /s/{slug}/{filename}, and
# registering it after the rest keeps it from shadowing anything narrower.
for _router in (system, designs, generate, sites, shop, publish):
    app.include_router(_router.router)
