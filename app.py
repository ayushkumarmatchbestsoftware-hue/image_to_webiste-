import os
import uuid
import json
import re
import asyncio
import random
import logging
import traceback
import sys
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, send_file, g, make_response, redirect
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
from google import genai
from PIL import Image
from bson import ObjectId
from config import Config
import uuid as pyuuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import insert, update, select, func
from core.db import get_session_factory
from core.mongo import insert_website_data, get_website_layout, update_website_layout, insert_chat_message, get_websites_collection
from core.r2 import upload_media_to_r2, R2_PUBLIC_URL, fetch_media_from_r2
from middleware.require_credits import require_credits
from handlers.credit_handler import website_credits_debits
from model.website_schema import WebsiteInfo, ChatMessage
from model.img_info_schema import ImageInfo
import os
import io
import shutil
import tempfile
from dotenv import load_dotenv
load_dotenv()

WEBSITE_AI_CREDIT_COST = os.getenv("WEBSITE_AI_CREDIT_COST")

# Ensure console logging does not crash on Windows when Unicode appears in log
# messages. This keeps the existing logs intact while preventing encoding errors.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# --- LOGGING SETUP ---
print(">>> [BOOT] App logging initializing...", flush=True)

from flask_cors import CORS

app = Flask(__name__)
# Fix for reverse proxy (Nginx/Cloudflare): tells Flask to trust X-Forwarded-Proto
# so all generated redirect URLs use https:// instead of http://.
# Without this, the iframe gets a http:// redirect which Chrome blocks as Mixed Content.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config.from_object(Config)
# Force Flask to ALWAYS generate https:// URLs — critical for deployments behind
# HTTPS-terminating proxies where X-Forwarded-Proto may not be set by Nginx.
app.config['PREFERRED_URL_SCHEME'] = 'https'
# Explicitly set max upload size (30MB) — Config class doesn't auto-apply to Flask
app.config['MAX_CONTENT_LENGTH'] = 30 * 1024 * 1024

# Enable terminal logging for all Flask requests and internal app logs
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s %(message)s')
root_logger = logging.getLogger()
app.logger.setLevel(logging.DEBUG)
app.logger.propagate = False
if not app.logger.handlers:
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.DEBUG)
    stream_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    app.logger.addHandler(stream_handler)

werkzeug_logger = logging.getLogger('werkzeug')
werkzeug_logger.setLevel(logging.DEBUG)
werkzeug_logger.propagate = False

@app.after_request
def log_response(response):
    app.logger.info('%s %s %s -> %s', request.remote_addr, request.method, request.path, response.status)
    return response

# --- CORS SECURITY ---
# Get allowed origins from .env (comma-separated if multiple)
# Default securely to local frontend testing only. NEVER put "*" here.
raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")
allowed_origins = [orig.strip() for orig in raw_origins.split(",") if orig.strip()]

CORS(app, origins=allowed_origins, supports_credentials=True)

# Global flag for lazy DB initialization
_DB_READY = False

# In-memory website context store — persists original AI data + layout per website_id
# so the /chat-edit route can re-render sections without requiring the user to re-generate
WEBSITE_CONTEXTS: dict = {}

# Feature flag to easily toggle chat-edit capabilities for Phase 2
ENABLE_CHAT_EDIT = os.getenv("ENABLE_CHAT_EDIT", "False").lower() == "true"

# --- DATABASE INITIALIZATION (Lazy, on first request only) ---
_db_initialized = False

def _ensure_db_initialized():
    """Initialize database on first request (synchronously)"""
    global _DB_READY, _db_initialized
    
    if _db_initialized:
        return
    
    _db_initialized = True
    
    if _DB_READY:
        return
    
    try:
        # For lazy init: try to get or create event loop
        import sys
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        if loop.is_running():
            # If already running, schedule as task (shouldn't happen in before_request)
            print(">>> [DB] Event loop already running, skipping init", flush=True)
            return
        
        from core.db import init_db
        loop.run_until_complete(init_db())
        _DB_READY = True
        print(">>> [DB] ✓ Database initialized on first request", flush=True)
    except Exception as e:
        print(f">>> [DB ERROR] Failed to initialize database: {e}", flush=True)
        # Don't crash server - DB init may fail for valid reasons (dev/test mode)

# --- AUTHENTICATION (Per-Request) ---
@app.before_request
def authenticate_request():
    """Authenticate user from token (query param, cookie, or header)"""
    global _DB_READY
    
    try:
        print(f"\n>>> [REQUEST] {request.method} {request.path}", flush=True)
        
        # Ensure DB is initialized on first request
        _ensure_db_initialized()

        # DEV_MODE: skip all auth, inject a fake user
        if app.config.get('DEV_MODE'):
            g.user_id = "00000000-0000-0000-0000-000000000001"
            g.user = None
            print(">>> [AUTH] DEV_MODE active — auth bypassed", flush=True)
            return

        # Try to extract and validate user from token
        from core.auth import get_current_user_flask
        try:
            user = get_current_user_flask()
            if user:
                g.user = user
                g.user_id = str(user.user_id)
                print(f">>> [AUTH] ✓ Authenticated: {g.user_id}", flush=True)
            else:
                print(f">>> [AUTH] ⚠ No auth token found for {request.path}", flush=True)
        except Exception as auth_err:
            print(f">>> [AUTH ERROR] Token parsing failed: {auth_err}", flush=True)
            g.user_id = None
            g.user = None
    except Exception as e:
        print(f">>> [REQUEST ERROR] Unexpected error in before_request: {e}", flush=True)
        import traceback
        traceback.print_exc()

def require_auth(f):
    """Decorator: Require authentication for a route"""
    from functools import wraps
    @wraps(f)
    async def decorated_function(*args, **kwargs):
        try:
            # DEV_MODE: bypass auth for local testing only
            if app.config.get('DEV_MODE'):
                if not getattr(g, 'user_id', None):
                    g.user_id = "00000000-0000-0000-0000-000000000001"
                print(f">>> [ROUTE] DEV_MODE: executing {f.__name__}", flush=True)
                return await f(*args, **kwargs)
            
            # Production: check for authenticated user
            user_id = getattr(g, 'user_id', None)
            if not user_id:
                print(f">>> [DENIED] {request.path} - No user_id in g (require_auth failed)", flush=True)
                return jsonify({"error": "Authentication required", "status": "error"}), 401
            
            print(f">>> [ROUTE] ✓ Auth passed for {f.__name__} by {user_id}", flush=True)
            return await f(*args, **kwargs)
        except Exception as e:
            print(f">>> [ROUTE ERROR] {f.__name__} failed: {e}", flush=True)
            import traceback
            traceback.print_exc()
            return jsonify({"error": "Internal server error", "status": "error"}), 500
    return decorated_function

# Initialize the new Google GenAI client with fail-safe handling
try:
    _api_key = app.config.get('GEMINI_API_KEY')
    if not _api_key:
        print(">>> [WARNING] GEMINI_API_KEY is missing! AI features will be disabled.", flush=True)
        genai_client = None
    else:
        genai_client = genai.Client(api_key=_api_key)
        print(">>> [INFO] Google GenAI client initialized successfully.", flush=True)
except Exception as e:
    print(f">>> [ERROR] GenAI Initialization failed: {e}", flush=True)
    genai_client = None

MAX_IMAGES = app.config["MAX_IMAGES"]

# ---------------------------------------------------------------------------
# NICHE → DESIGN TOKENS
# ---------------------------------------------------------------------------
NICHE_DESIGN = {
    "restaurant":   {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#4338ca", "primary_dark": "#312e81", "bg": "#fffcf9", "bg_alt": "#f6f3f0", "text_main": "#1e1b4b", "text_muted": "#4338ca", "accent": "#f59e0b", "mood": "boutique dining"},
    "cafe":         {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#7c2d12", "primary_dark": "#431407", "bg": "#fdfaf7", "bg_alt": "#f5f0eb", "text_main": "#431407", "text_muted": "#9a3412", "accent": "#10b981", "mood": "artisanal coffee"},
    "bakery":       {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#92400e", "primary_dark": "#78350f", "bg": "#fffcf9", "bg_alt": "#f7f3f0", "text_main": "#451a03", "text_muted": "#b45309", "accent": "#f59e0b", "mood": "handcrafted patisserie"},
    "bar":          {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#4f46e5", "primary_dark": "#3730a3", "bg": "#0a0a0a", "bg_alt": "#141414", "text_main": "#fafafa", "text_muted": "#818cf8", "accent": "#f59e0b", "mood": "speakeasy sophisticated"},
    "hotel":        {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#0f172a", "primary_dark": "#022c22", "bg": "#faf9f6", "bg_alt": "#f2f0eb", "text_main": "#0f172a", "text_muted": "#334155", "accent": "#f59e0b", "mood": "curated hospitality"},
    "spa":          {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#065f46", "primary_dark": "#064e3b", "bg": "#fcfaf8", "bg_alt": "#f4f0ec", "text_main": "#064e3b", "text_muted": "#059669", "accent": "#f59e0b", "mood": "editorial wellness"},
    "yoga":         {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#166534", "primary_dark": "#14532d", "bg": "#f9f7f2", "bg_alt": "#f1eee6", "text_main": "#14532d", "text_muted": "#15803d", "accent": "#ea580c", "mood": "organic minimal"},
    "fitness":      {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#dc2626", "primary_dark": "#991b1b", "bg": "#ffffff", "bg_alt": "#f4f4f5", "text_main": "#111827", "text_muted": "#4b5563", "accent": "#000000", "mood": "architectural performance"},
    "gym":          {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#dc2626", "primary_dark": "#991b1b", "bg": "#ffffff", "bg_alt": "#f4f4f5", "text_main": "#111827", "text_muted": "#4b5563", "accent": "#000000", "mood": "raw architectural"},
    "wellness":     {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#1e3a2f", "primary_dark": "#0c1a14", "bg": "#fdfbf7", "bg_alt": "#f5f2ed", "text_main": "#1e3a2f", "text_muted": "#4a5d52", "accent": "#f59e0b", "mood": "natural boutique"},
    "medical":      {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#2563eb", "primary_dark": "#1e40af", "bg": "#ffffff", "bg_alt": "#f8fafc", "text_main": "#0f172a", "text_muted": "#3b82f6", "accent": "#f59e0b", "mood": "premium clinical"},
    "dental":       {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#2563eb", "primary_dark": "#1e40af", "bg": "#ffffff", "bg_alt": "#f8fafc", "text_main": "#0f172a", "text_muted": "#0ea5e9", "accent": "#f59e0b", "mood": "clean professional"},
    "law":          {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#1e293b", "primary_dark": "#0f172a", "bg": "#fcfcfc", "bg_alt": "#f1f1f1", "text_main": "#0f172a", "text_muted": "#475569", "accent": "#94a3b8", "mood": "authoritative minimalist"},
    "finance":      {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#064e3b", "primary_dark": "#022c22", "bg": "#ffffff", "bg_alt": "#f8fafc", "text_main": "#064e3b", "text_muted": "#059669", "accent": "#f59e0b", "mood": "premium stability"},
    "consulting":   {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#1e1b4b", "primary_dark": "#0c0a09", "bg": "#ffffff", "bg_alt": "#f8fafc", "text_main": "#1e1b4b", "text_muted": "#4338ca", "accent": "#f59e0b", "mood": "strategic minimalist"},
    "real estate":  {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#334155", "primary_dark": "#1e293b", "bg": "#fdfcfa", "bg_alt": "#f4f2ef", "text_main": "#1e293b", "text_muted": "#475569", "accent": "#f59e0b", "mood": "editorial residential"},
    "photography":  {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#000000", "primary_dark": "#000000", "bg": "#ffffff", "bg_alt": "#f4f4f5", "text_main": "#111827", "text_muted": "#4b5563", "accent": "#2563eb", "mood": "cinematic editorial"},
    "fashion":      {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#db2777", "primary_dark": "#9d174d", "bg": "#ffffff", "bg_alt": "#f4f4f5", "text_main": "#111827", "text_muted": "#be185d", "accent": "#000000", "mood": "fashion editorial"},
    "interior":     {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#4338ca", "primary_dark": "#312e81", "bg": "#fffcf9", "bg_alt": "#f6f3f0", "text_main": "#1e1b4b", "text_muted": "#4338ca", "accent": "#f59e0b", "mood": "architectural luxury"},
    "architecture": {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#1e1b4b", "primary_dark": "#0c0a09", "bg": "#ffffff", "bg_alt": "#f4f4f5", "text_main": "#09090b", "text_muted": "#4b5563", "accent": "#2563eb", "mood": "structural architectural"},
    "design":       {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#1e1b4b", "primary_dark": "#0c0a09", "bg": "#ffffff", "bg_alt": "#f4f4f5", "text_main": "#09090b", "text_muted": "#4b5563", "accent": "#2563eb", "mood": "modern experimental"},
    "saas":         {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#4338ca", "primary_dark": "#312e81", "bg": "#ffffff", "bg_alt": "#f8fafc", "text_main": "#1e1b4b", "text_muted": "#4338ca", "accent": "#f59e0b", "mood": "technical premium"},
    "software":     {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#4338ca", "primary_dark": "#312e81", "bg": "#ffffff", "bg_alt": "#f8fafc", "text_main": "#1e1b4b", "text_muted": "#4338ca", "accent": "#10b981", "mood": "precise engineering"},
    "startup":      {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#4338ca", "primary_dark": "#312e81", "bg": "#ffffff", "bg_alt": "#f8fafc", "text_main": "#1e1b4b", "text_muted": "#4338ca", "accent": "#f59e0b", "mood": "disruptive ambitious"},
    "ai":           {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#4338ca", "primary_dark": "#312e81", "bg": "#ffffff", "bg_alt": "#f8fafc", "text_main": "#1e1b4b", "text_muted": "#4338ca", "accent": "#4f46e5", "mood": "premium intelligent"},
    "tech":         {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#4338ca", "primary_dark": "#312e81", "bg": "#ffffff", "bg_alt": "#f4f4f5", "text_main": "#111827", "text_muted": "#4338ca", "accent": "#1a1a1a", "mood": "structural engineering"},
    "agency":       {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#4338ca", "primary_dark": "#312e81", "bg": "#ffffff", "bg_alt": "#f4f4f5", "text_main": "#111827", "text_muted": "#4338ca", "accent": "#1a1a1a", "mood": "premium minimalist"},
    "school":       {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#1e40af", "primary_dark": "#1e3a8a", "bg": "#ffffff", "bg_alt": "#f8fafc", "text_main": "#1e293b", "text_muted": "#3b82f6", "accent": "#f59e0b", "mood": "modern academic"},
    "coaching":     {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#1e1b4b", "primary_dark": "#0c0a09", "bg": "#fafafa", "bg_alt": "#f4f4f5", "text_main": "#18181b", "text_muted": "#4338ca", "accent": "#4f46e5", "mood": "visionary leadership"},
    "nonprofit":    {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#064e3b", "primary_dark": "#022c22", "bg": "#ffffff", "bg_alt": "#f0fdf4", "text_main": "#064e3b", "text_muted": "#10b981", "accent": "#f59e0b", "mood": "impactful boutique"},
    "wedding":      {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#7c2d12", "primary_dark": "#431407", "bg": "#fffdfa", "bg_alt": "#fdf5e6", "text_main": "#431407", "text_muted": "#9a3412", "accent": "#f59e0b", "mood": "cinematic romantic"},
    "event":        {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#dc2626", "primary_dark": "#991b1b", "bg": "#ffffff", "bg_alt": "#f4f4f5", "text_main": "#111827", "text_muted": "#4b5563", "accent": "#000000", "mood": "bold impact"},
    "jewelry":      {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#1c1917", "primary_dark": "#000000", "bg": "#ffffff", "bg_alt": "#fafaf9", "text_main": "#1c1917", "text_muted": "#78716c", "accent": "#f59e0b", "mood": "quiet luxury"},
    "clothing":     {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#000000", "primary_dark": "#000000", "bg": "#ffffff", "bg_alt": "#f4f4f5", "text_main": "#111827", "text_muted": "#4b5563", "accent": "#2563eb", "mood": "editorial fashion"},
    "ecommerce":    {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#1e1b4b", "primary_dark": "#0c0a09", "bg": "#ffffff", "bg_alt": "#f4f4f5", "text_main": "#111827", "text_muted": "#4b5563", "accent": "#2563eb", "mood": "premium retail"},
    "shop":         {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#1e1b4b", "primary_dark": "#0c0a09", "bg": "#ffffff", "bg_alt": "#f4f4f5", "text_main": "#111827", "text_muted": "#4b5563", "accent": "#2563eb", "mood": "modern retail"},
    "food":         {"font_heading": "Roboto",             "font_body": "Roboto",            "primary": "#991b1b", "primary_dark": "#7f1d1d", "bg": "#fffcf9", "bg_alt": "#fef2f2", "text_main": "#7f1d1d", "text_muted": "#dc2626", "accent": "#f59e0b", "mood": "artisanal quality"},
}

LAYOUT_POOLS = {
    "restaurant":  [
        ["hero", "about", "services", "testimonials", "portfolio", "contact"],
        ["hero", "services", "about", "portfolio", "contact"],
        ["hero", "portfolio", "services", "about", "testimonials", "contact"]
    ],
    "cafe":        [
        ["hero", "about", "services", "portfolio", "contact"],
        ["hero", "services", "about", "contact"]
    ],
    "bakery":      [
        ["hero", "services", "about", "portfolio", "contact"],
        ["hero", "about", "services", "contact"]
    ],
    "bar":         [
        ["hero", "about", "services", "portfolio", "contact"],
        ["hero", "services", "about", "contact"]
    ],
    "hotel":       [
        ["hero", "services", "about", "testimonials", "contact"],
        ["hero", "about", "services", "portfolio", "contact"]
    ],
    "spa":         [
        ["hero", "about", "services", "testimonials", "contact"],
        ["hero", "services", "about", "contact"]
    ],
    "yoga":        [
        ["hero", "about", "services", "testimonials", "contact"],
        ["hero", "services", "about", "contact"]
    ],
    "fitness":     [
        ["hero", "stats", "services", "about", "testimonials", "contact"],
        ["hero", "about", "services", "stats", "contact"]
    ],
    "gym":         [
        ["hero", "stats", "services", "about", "portfolio", "contact"],
        ["hero", "services", "about", "stats", "contact"]
    ],
    "law":         [
        ["hero", "about", "services", "testimonials", "faq", "contact"],
        ["hero", "services", "about", "faq", "contact"]
    ],
    "finance":     [
        ["hero", "about", "services", "stats", "testimonials", "contact"],
        ["hero", "services", "about", "stats", "contact"]
    ],
    "consulting":  [
        ["hero", "services", "about", "testimonials", "faq", "contact"],
        ["hero", "about", "services", "faq", "contact"]
    ],
    "saas":        [
        ["hero", "stats", "services", "about", "pricing", "faq", "contact"],
        ["hero", "services", "about", "pricing", "contact"]
    ],
    "startup":     [
        ["hero", "stats", "services", "about", "testimonials", "contact"],
        ["hero", "about", "services", "stats", "contact"]
    ],
    "software":    [
        ["hero", "services", "about", "pricing", "faq", "contact"],
        ["hero", "about", "services", "pricing", "contact"]
    ],
    "tech":        [
        ["hero", "stats", "services", "about", "portfolio", "contact"],
        ["hero", "services", "about", "stats", "contact"]
    ],
    "agency":      [
        ["hero", "portfolio", "services", "about", "testimonials", "contact"],
        ["hero", "about", "services", "portfolio", "contact"]
    ],
    "photography": [
        ["hero", "portfolio", "about", "services", "testimonials", "contact"],
        ["hero", "about", "portfolio", "services", "contact"]
    ],
    "fashion":     [
        ["hero", "portfolio", "about", "services", "contact"],
        ["hero", "about", "portfolio", "services", "contact"]
    ],
    "ecommerce":   [
        ["hero", "services", "portfolio", "testimonials", "contact"],
        ["hero", "portfolio", "services", "contact"]
    ],
    "medical":     [
        ["hero", "services", "about", "faq", "contact"],
        ["hero", "about", "services", "faq", "contact"]
    ],
    "dental":      [
        ["hero", "services", "about", "testimonials", "faq", "contact"],
        ["hero", "about", "services", "faq", "contact"]
    ],
    "real estate": [
        ["hero", "services", "portfolio", "about", "testimonials", "contact"],
        ["hero", "portfolio", "about", "services", "contact"]
    ],
    "default":     [
        ["hero", "about", "services", "portfolio", "contact"],
        ["hero", "services", "about", "portfolio", "contact"],
        ["hero", "about", "services", "contact"]
    ],
}

def get_niche_key(prompt: str) -> str:
    p = prompt.lower()
    for kw in NICHE_DESIGN:
        if kw in p:
            return kw
    return None

def get_fallback_tokens(prompt: str) -> dict:
    key = get_niche_key(prompt)
    return NICHE_DESIGN.get(key, {
        "font_heading": "Roboto", "font_body": "Roboto",
        "primary": "#4f46e5", "primary_dark": "#312e81",
        "bg": "#ffffff", "bg_alt": "#f8fafc",
        "text_main": "#0f172a", "text_muted": "#475569",
        "accent": "#f59e0b", "mood": "strategic minimalist"
    })

def get_layout_blueprint(prompt: str) -> list:
    p = prompt.lower()
    for kw, layouts in LAYOUT_POOLS.items():
        if kw in p:
            return random.choice(layouts)
    return random.choice(LAYOUT_POOLS["default"])

# ---------------------------------------------------------------------------
# PALETTE MAP — user's named palette overrides niche fallback
# ---------------------------------------------------------------------------
PALETTE_MAP = {
    "luxury":    {"primary": "#c9a96e", "primary_dark": "#a07840", "bg": "#0f0f0f", "bg_alt": "#1a1a1a", "text_main": "#f5f0e8", "text_muted": "#b8a98a", "accent": "#c9a96e"},
    "brutalist": {"primary": "#000000", "primary_dark": "#000000", "bg": "#ffffff", "bg_alt": "#f0f0f0", "text_main": "#000000", "text_muted": "#444444", "accent": "#f5c842"},
    "soft":      {"primary": "#c2848a", "primary_dark": "#a06068", "bg": "#fdf6f0", "bg_alt": "#f9ede4", "text_main": "#4a2f35", "text_muted": "#9a6f75", "accent": "#f0b8bc"},
    "dark":      {"primary": "#7c6af7", "primary_dark": "#5b48e0", "bg": "#0d0d14", "bg_alt": "#14141f", "text_main": "#e8e6ff", "text_muted": "#8b87c0", "accent": "#7c6af7"},
    "earthy":    {"primary": "#9b6a44", "primary_dark": "#7a4f2e", "bg": "#f5f0e8", "bg_alt": "#ede5d8", "text_main": "#2d1f10", "text_muted": "#7a6045", "accent": "#c4894e"},
    "neon":      {"primary": "#00ffcc", "primary_dark": "#00c9a0", "bg": "#050510", "bg_alt": "#0a0a1a", "text_main": "#e0fffa", "text_muted": "#00ffcc", "accent": "#ff2d78"},
    # legacy names kept for backwards compatibility
    "midnight":  {"primary": "#4338ca", "primary_dark": "#312e81", "bg": "#ffffff", "bg_alt": "#f8fafc", "text_main": "#1e1b4b", "text_muted": "#4338ca", "accent": "#f59e0b"},
    "slate":     {"primary": "#334155", "primary_dark": "#1e293b", "bg": "#fdfcfa", "bg_alt": "#f4f2ef", "text_main": "#1e293b", "text_muted": "#475569", "accent": "#f59e0b"},
    "crimson":   {"primary": "#991b1b", "primary_dark": "#7f1d1d", "bg": "#fffcf9", "bg_alt": "#fef2f2", "text_main": "#7f1d1d", "text_muted": "#dc2626", "accent": "#f59e0b"},
    "forest":    {"primary": "#065f46", "primary_dark": "#064e3b", "bg": "#fcfaf8", "bg_alt": "#f4f0ec", "text_main": "#064e3b", "text_muted": "#059669", "accent": "#f59e0b"},
    "ocean":     {"primary": "#1e40af", "primary_dark": "#1e3a8a", "bg": "#ffffff", "bg_alt": "#f8fafc", "text_main": "#1e293b", "text_muted": "#3b82f6", "accent": "#f59e0b"},
    "gold":      {"primary": "#92400e", "primary_dark": "#78350f", "bg": "#fffcf9", "bg_alt": "#f7f3f0", "text_main": "#451a03", "text_muted": "#b45309", "accent": "#10b981"},
    "rose":      {"primary": "#9d174d", "primary_dark": "#831843", "bg": "#ffffff", "bg_alt": "#fdf2f8", "text_main": "#500724", "text_muted": "#be185d", "accent": "#f59e0b"},
    "onyx":      {"primary": "#1c1917", "primary_dark": "#000000", "bg": "#ffffff", "bg_alt": "#fafaf9", "text_main": "#1c1917", "text_muted": "#78716c", "accent": "#f59e0b"},
}

# ---------------------------------------------------------------------------
# COLOR VALIDATOR — auto-corrects bad Gemini theme values
# ---------------------------------------------------------------------------
def hex_to_rgb(hex_color: str):
    """Convert #rrggbb to (r, g, b) tuple. Returns None on failure."""
    try:
        h = hex_color.strip().lstrip('#')
        if len(h) == 3:
            h = ''.join(c*2 for c in h)
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    except Exception:
        return None

def relative_luminance(r, g, b):
    vals = []
    for c in [r, g, b]:
        c /= 255.0
        vals.append(c/12.92 if c <= 0.03928 else ((c+0.055)/1.055)**2.4)
    return 0.2126*vals[0] + 0.7152*vals[1] + 0.0722*vals[2]

def contrast_ratio(hex1: str, hex2: str) -> float:
    rgb1 = hex_to_rgb(hex1)
    rgb2 = hex_to_rgb(hex2)
    if not rgb1 or not rgb2:
        return 1.0
    l1 = relative_luminance(*rgb1)
    l2 = relative_luminance(*rgb2)
    lighter = max(l1, l2)
    darker  = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)

def is_dark(hex_color: str) -> bool:
    rgb = hex_to_rgb(hex_color)
    if not rgb:
        return False
    return relative_luminance(*rgb) < 0.3

def rgba_from_hex(hex_color: str, opacity: float = 0.12) -> str:
    rgb = hex_to_rgb(hex_color)
    if not rgb:
        return f"rgba(0,0,0,{opacity})"
    return f"rgba({rgb[0]},{rgb[1]},{rgb[2]},{opacity})"

def validate_and_fix_theme(theme: dict, fallback: dict, has_image: bool = False) -> dict:
    """
    Auto-corrects every bad Gemini theme value using pre-defined fallback tokens.
    This is the single source of truth for theme correctness.
    """
    fixed = dict(theme)

    # 1. Fill missing keys from fallback
    required = ["primary", "primary_dark", "bg", "bg_alt", "text_main", "text_muted",
                "accent", "font_heading", "font_body", "hero_style", "card_style", "divider_style"]
    for key in required:
        if not fixed.get(key):
            fixed[key] = fallback.get(key, "")

    # 2. Always recompute primary_light from primary
    fixed["primary_light"] = rgba_from_hex(fixed.get("primary", "#111111"), 0.12)

    # 3. Always recompute nav_bg from bg
    bg = fixed.get("bg", "#ffffff")
    bg_rgb = hex_to_rgb(bg)
    if bg_rgb:
        fixed["nav_bg"] = f"rgba({bg_rgb[0]},{bg_rgb[1]},{bg_rgb[2]},0.92)"
    else:
        fixed["nav_bg"] = "rgba(255,255,255,0.92)"

    # 4. Fix bg if it is a mid-tone brand color (Gemini sometimes sets bg = primary)
    # bg should be light (luminance > 0.5) OR very dark (luminance < 0.1) — never mid-tone
    bg_lum = relative_luminance(*bg_rgb) if bg_rgb else 1.0
    primary_hex = fixed.get("primary", "#111111")
    bg_too_similar_to_primary = False
    if bg_rgb:
        p_rgb = hex_to_rgb(primary_hex)
        if p_rgb:
            # If bg and primary are too similar in luminance, bg is wrong
            p_lum = relative_luminance(*p_rgb)
            if abs(bg_lum - p_lum) < 0.15:
                bg_too_similar_to_primary = True

    if bg_too_similar_to_primary:
        fixed["bg"]     = fallback.get("bg", "#f8f8f8")
        fixed["bg_alt"] = fallback.get("bg_alt", "#efefef")
        bg = fixed["bg"]
        bg_rgb = hex_to_rgb(bg)
        bg_lum = relative_luminance(*bg_rgb) if bg_rgb else 1.0
        if bg_rgb:
            fixed["nav_bg"] = f"rgba({bg_rgb[0]},{bg_rgb[1]},{bg_rgb[2]},0.92)"

    # 5. Fix text_main contrast against bg
    text_main = fixed.get("text_main", "#111111")
    if contrast_ratio(text_main, bg) < 4.5:
        # Dark bg needs light text, light bg needs dark text
        if bg_lum < 0.4:
            fixed["text_main"]  = "#f5f5f5"
            fixed["text_muted"] = "rgba(255,255,255,0.62)"
        else:
            fixed["text_main"]  = fallback.get("text_main", "#111111")
            fixed["text_muted"] = fallback.get("text_muted", "#555555")

    # 6. bg and bg_alt must be different
    if fixed.get("bg") == fixed.get("bg_alt"):
        fixed["bg_alt"] = fallback.get("bg_alt", "#f0f0f0")

    # 7. hero_style logic
    #    - fullbleed: ONLY when user has uploaded an image (looks great with real photo)
    #    - dark bg niches (gym, bar, music etc): bold-center (text on dark bg, no empty right panel)
    #    - light bg niches: split-left (text left, visual/card right)
    # 7. hero_style logic
    #    - fullbleed: ONLY when user has uploaded an image (looks great with real photo)
    #    - dark bg niches (gym, bar, music etc): bold-center (text on dark bg, no empty right panel)
    #    - light bg niches: split-left (text left, visual/card right)
    if fixed.get("hero_style") not in ["split-left", "fullbleed", "bold-center"]:
        fixed["hero_style"] = "split-left"
    
    if not has_image:
        # If no image, we want to avoid the "blank right side" look.
        # bold-center is the safest/most professional looking layout without imagery.
        fixed["hero_style"] = "bold-center"
    elif fixed["hero_style"] == "fullbleed":
        # fullbleed is fine if we have an image
        pass

    # 8. card_style
    if fixed.get("card_style") not in ["flat", "outlined", "elevated"]:
        fixed["card_style"] = "elevated"

    # 9. divider_style
    if fixed.get("divider_style") not in ["diagonal", "wave", "none"]:
        fixed["divider_style"] = "none"

    # 10. Font guards — DEFAULT IS ROBOTO
    # Only allow other fonts if they are top-tier and explicitly in theme
    top_tier = ["Roboto", "Plus Jakarta Sans", "Outfit", "Fraunces", "Playfair Display"]
    if not fixed.get("font_heading") or fixed["font_heading"] not in top_tier:
        fixed["font_heading"] = fallback.get("font_heading", "Roboto")
    if not fixed.get("font_body") or fixed["font_body"] not in top_tier:
        fixed["font_body"] = fallback.get("font_body", "Roboto")

    return fixed

# ---------------------------------------------------------------------------
# SYSTEM PROMPT
# ---------------------------------------------------------------------------
system_prompt = """
You are a Distinguished Creative Director and Brand Architect for an elite global agency.
Your task is to materialize a website that feels meticulously handcrafted, professional, and entirely human.

CRITICAL: ZERO AI LOOK.
1. ABSOLUTELY NO generic marketing phrases ("Your partner in...", "One-stop shop", "Industry leader").
2. USE SPECIALIZED JARGON. If it's a tech company, use terms like "Orchestration Layer", "State Management", "Concurrency". If it's a law firm, use "Transactional Integrity", "Litigation Strategy".
3. COLOR IS MANDATORY. Every design must be vibrant and professional, using rich premium colors (Indigo, Slate, Crimson, Forest, etc.) paired with the Roboto typography system.
4. COPY MUST BE DECISIVE. No "Welcome to". No "Discover". Start with facts and powerful industry statements.

CRITICAL COPY RULES:
- BAD title: "Delicate Patisserie in Blush Pinks and Warm Whites"  ← mentions colors
- GOOD title: "Every Bite, A Moment to Savour"  ← real marketing copy
- BAD subtitle: "Crafted in warm terracotta tones"  ← mentions palette
- GOOD subtitle: "Handmade pastries baked fresh every morning using local flour."

Return ONLY valid JSON. No markdown fences. No text outside JSON.

{
    "theme": {
    "font_heading": "Roboto",
    "font_body": "Roboto",
    "primary": "#hex",
    "primary_dark": "#hex",
    "primary_light": "rgba(r,g,b,0.12)",
    "accent": "#hex",
    "bg": "#hex",
    "bg_alt": "#hex slightly different from bg",
    "text_main": "#hex dark enough for 4.5:1 contrast on bg",
    "text_muted": "#hex",
    "nav_bg": "rgba(r,g,b,0.92)",
    "divider_style": "diagonal",
    "hero_bg_id": "Unsplash ID that best fits the niche (Minimal/Modern Architectural style)",
    "about_bg_id": "Unsplash ID that best fits the niche (different from hero_bg_id)"
  },
  "site_info": {
    "display_name": "2 word business name max",
    "site_title": "Brand tagline — NOT a color or design description",
    "tagline": "One punchy emotional line"
  },
  "layout": ["hero","about","services","portfolio","contact"],
  "home": {
    "label": "2-4 word eyebrow label",
    "title": "Powerful headline about the BUSINESS VALUE",
    "subtitle": "One compelling sentence about what makes this business special",
    "cta": "Action CTA verb + benefit",
    "cta2": "Secondary CTA"
  },
  "about": {
    "label": "Eyebrow label",
    "heading": "Story-driven heading",
    "description": "2-3 specific human sentences. Concrete real details. No generic phrases.",
    "stat1_number": "12+", "stat1_label": "Years Experience",
    "stat2_number": "340+", "stat2_label": "Happy Clients",
    "stat3_number": "97%", "stat3_label": "Satisfaction Rate"
  },
  "services": [
    {"title": "Real service name", "description": "2 specific sentences.", "icon_type": "shield"},
    {"title": "Real service name", "description": "2 sentences.", "icon_type": "star"},
    {"title": "Real service name", "description": "2 sentences.", "icon_type": "zap"},
    {"title": "Real service name", "description": "2 sentences.", "icon_type": "globe"}
  ],
  "portfolio": [
    {"title": "Bespoke Strategy", "description": "Architectural solution for a high-traffic system.", "tag": "Industrial Design", "client": "Global Logistics Corp", "outcome": "30% increase in regional distribution efficiency."},
    {"title": "Bespoke Strategy", "description": "Architectural solution for a high-traffic system.", "tag": "Industrial Design", "client": "Global Logistics Corp", "outcome": "30% increase in regional distribution efficiency."},
    {"title": "Bespoke Strategy", "description": "Architectural solution for a high-traffic system.", "tag": "Industrial Design", "client": "Global Logistics Corp", "outcome": "30% increase in regional distribution efficiency."}
  ],
  "testimonials": [
    {"name": "Full Name", "role": "Title, Company", "text": "Specific 2-sentence review with real details.", "rating": 5},
    {"name": "Full Name", "role": "Title, Company", "text": "Specific review.", "rating": 5},
    {"name": "Full Name", "role": "Title, Company", "text": "Specific review.", "rating": 5}
  ],
  "faq": [
    {"question": "Real question for this business type", "answer": "Clear 2-sentence answer."},
    {"question": "Real question", "answer": "Clear answer."},
    {"question": "Real question", "answer": "Clear answer."},
    {"question": "Real question", "answer": "Clear answer."}
  ],
  "pricing": [
    {"name": "Plan", "price": "$99", "period": "/month", "description": "Who it's for.", "features": ["Feature 1","Feature 2","Feature 3"], "highlighted": false},
    {"name": "Plan", "price": "$199", "period": "/month", "description": "Who it's for.", "features": ["Everything in Basic","Feature 4","Feature 5"], "highlighted": true},
    {"name": "Plan", "price": "$399", "period": "/month", "description": "Who it's for.", "features": ["Everything in Pro","Feature 6","Feature 7"], "highlighted": false}
  ],
  "stats": [
    {"number": "10K+", "label": "Members"},
    {"number": "4.9★", "label": "Rating"},
    {"number": "15+", "label": "Trainers"},
    {"number": "6AM-11PM", "label": "Open Daily"}
  ],
  "contact": {
    "label": "Eyebrow label",
    "title": "Warm inviting heading",
    "description": "1-2 specific sentences for this business.",
    "email": "hello@business.com",
    "phone": "+1 (555) 000-0000",
    "address": "City, Country"
  },
  "footer": {"tagline": "Memorable sign-off"}
}

1. Return ONLY JSON. No markdown. No text outside.
2. Fonts MUST be 'Roboto' for both heading and body by DEFAULT.
3. Use RICH, VIBRANT colors in the theme. Avoid "clinical" black/white.
4. ALL copy must be niche-perfect. Use the specific vocabulary of a high-end professional in that field.
5. PORTFOLIO: Each item MUST have: 'tag' (niche expertise), 'title', 'description' (the HOW), 'client' (industry name), and 'outcome' (the RESULTS).
   - NEVER use "Project", "Case Study", or "Service" as tags.
6. NO AI-ISMS: Ban "Unlock", "Empower", "Comprehensive", "Seamless", "Journey", "Elevate".
7. Content length must be varied (some punchy, some detailed) to look realistic.
8. Use realistic, invented dummy content instead of [Placeholders].
9. hero_style: "split-left", "fullbleed", or "bold-center"
10. card_style: "flat", "outlined", or "elevated"
11. divider_style: "diagonal", "wave", or "none"
12. SERVICES: ALWAYS generate EXACTLY 4 services to ensure a balanced 2x2 or 4-column grid.
13. BACKGROUNDS: If no images are provided, select professional Unsplash IDs for 'hero_bg_id' and 'about_bg_id'. Choose high-end architectural, nature, or abstract textures that complement the business niche. NO generic stock photos.
"""

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['GENERATED_FOLDER'], exist_ok=True)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']


# ---------------------------------------------------------------------------
# INDUSTRY TEMPLATES  — the secret design intelligence
# When user picks an industry, this block is secretly injected into the AI
# prompt, pre-wiring sections, tone, fonts, CTAs, and sensory language.
# ---------------------------------------------------------------------------
INDUSTRY_TEMPLATES = {
    "restaurant": {
        "label": "Restaurant / Food",
        "inject": """INDUSTRY DESIGN BRIEF: Restaurant & Dining
VISUAL DIRECTION: Warm, sensory, and inviting. Make visitors feel hungry and welcome.
TONE: Artisan, neighborhood, beloved. Use words like: crafted, seasonal, sourced, slow-fermented, wood-fired, local.
SECTIONS TO PRIORITIZE: Dramatic full-bleed hero with atmosphere shot, chef story in About, menu categories in Services, diner reviews in Testimonials, opening hours + reservation in Contact.
KEY CTAs: 'Reserve a Table', 'View Menu', 'Book Private Dining', 'Order Online'
FONTS DIRECTION: Serif headings for warmth (Playfair Display or Fraunces). Clean body font.
PALETTE DIRECTION: Warm ivory backgrounds, deep burgundy or forest green primary, gold accents.
BANNED WORDS: fast, quick, efficient, solution, platform, scalable.""",
        "default_sections": ["hero", "about", "services", "stats", "testimonials", "contact"],
        "palette_hint": "earthy",
    },
    "saas": {
        "label": "SaaS / Tech",
        "inject": """INDUSTRY DESIGN BRIEF: SaaS & Technology Product
VISUAL DIRECTION: Precise, technical, confident. Dark or clean-light aesthetic with sharp product logic.
TONE: Direct, technical, no-nonsense. Use specialist terms: orchestration, API-first, rate limiting, concurrency, webhooks, state management.
SECTIONS TO PRIORITIZE: Conversion hero with product screenshot/dashboard, feature grid in Services, social proof stats (users, uptime %, requests/sec), pricing tiers, testimonials from engineering leads.
KEY CTAs: 'Start Free Trial', 'View Docs', 'Book a Demo', 'See Pricing'
FONTS DIRECTION: Modern geometric sans-serif. Outfit or Plus Jakarta Sans.
PALETTE DIRECTION: Deep navy or near-black bg, indigo or electric blue primary, white text on dark.
BANNED WORDS: journey, warm, artisan, cozy, beloved, handcrafted.""",
        "default_sections": ["hero", "services", "stats", "testimonials", "pricing", "contact"],
        "palette_hint": "dark",
    },
    "law": {
        "label": "Law Firm / Legal",
        "inject": """INDUSTRY DESIGN BRIEF: Law Firm & Legal Services
VISUAL DIRECTION: Authority, gravitas, and trust. Clean, structured, powerful. Never flashy.
TONE: Authoritative and precise. Use specialist terms: litigation strategy, transactional integrity, due diligence, fiduciary duty, case precedent, motion practice, pro bono.
SECTIONS TO PRIORITIZE: Strong editorial hero (no stock imagery feel), practice areas in Services, partner/attorney profiles in About, client testimonials, consultation CTA + office address in Contact.
KEY CTAs: 'Schedule Consultation', 'View Practice Areas', 'Meet Our Attorneys', 'Submit a Case'
FONTS DIRECTION: Classic serif for headings (Playfair Display or Fraunces). Clean body.
PALETTE DIRECTION: Deep navy or dark slate primary, warm cream or white background, gold accent line.
BANNED WORDS: awesome, amazing, cool, fun, cozy, vibrant, affordable.""",
        "default_sections": ["hero", "about", "services", "testimonials", "stats", "contact"],
        "palette_hint": "luxury",
    },
    "agency": {
        "label": "Agency / Studio",
        "inject": """INDUSTRY DESIGN BRIEF: Creative Agency & Design Studio
VISUAL DIRECTION: The site itself is the portfolio statement. Bold, experimental, editorial. White space is structure.
TONE: Opinionated, curatorial, confident. Use terms: brand identity, visual systems, motion language, typographic hierarchy, spatial composition, creative strategy.
SECTIONS TO PRIORITIZE: Bold editorial hero (no image, strong typography), portfolio case studies with outcomes, services (branding, digital, motion, strategy), team with titles, inquiry form.
KEY CTAs: 'View Case Studies', 'Start a Project', 'See Our Work', 'New Project Inquiry'
FONTS DIRECTION: Contrasting type pairing. Bold display heading, neutral body. Fraunces or Outfit.
PALETTE DIRECTION: High-contrast B&W with one vivid accent (electric blue, deep red, or neon yellow).
BANNED WORDS: solution, comprehensive, affordable, scalable, synergy.""",
        "default_sections": ["hero", "portfolio", "services", "about", "testimonials", "contact"],
        "palette_hint": "brutalist",
    },
    "spa": {
        "label": "Spa / Wellness",
        "inject": """INDUSTRY DESIGN BRIEF: Spa, Wellness & Beauty
VISUAL DIRECTION: Serene, minimal, unhurried. Every pixel breathes. Negative space is intentional.
TONE: Gentle, considered, restorative. Use terms: botanical, meridian, somatic, restorative, mindful ritual, sensory journey, tissue depth, lymphatic.
SECTIONS TO PRIORITIZE: Serene atmospheric hero, treatment menu in Services, philosophy/founder story in About, client testimonials, booking form in Contact.
KEY CTAs: 'Book a Treatment', 'View Menu', 'Gift a Session', 'Reserve Your Time'
FONTS DIRECTION: Elegant and airy. Fraunces italic for headings, clean body.
PALETTE DIRECTION: Warm stone, blush, sage or natural linen tones. Never dark.
BANNED WORDS: tech, scalable, efficient, ROI, leverage, platform, bold.""",
        "default_sections": ["hero", "services", "about", "testimonials", "stats", "contact"],
        "palette_hint": "soft",
    },
    "gym": {
        "label": "Gym / Fitness",
        "inject": """INDUSTRY DESIGN BRIEF: Gym, Fitness & Performance Training
VISUAL DIRECTION: High-energy, powerful, raw. Dark bg with bold typography. Movement and momentum.
TONE: Direct, challenging, results-oriented. Use terms: rep maxes, periodization, functional threshold, VO2 max, progressive overload, sport-specific conditioning.
SECTIONS TO PRIORITIZE: Bold dark hero with power statement, programs/training styles in Services, coach credentials in About, transformation stats, member testimonials, membership pricing, Contact.
KEY CTAs: 'Join Now', 'Book Free Trial Class', 'See Programs', 'Get Your Plan'
FONTS DIRECTION: Heavy grotesque or condensed sans-serif. Outfit ExtraBold headings.
PALETTE DIRECTION: Near-black background, electric or deep primary (red, orange, or electric blue), white text.
BANNED WORDS: gentle, serene, mindful, relaxing, delicate, elegant.""",
        "default_sections": ["hero", "services", "stats", "about", "testimonials", "pricing", "contact"],
        "palette_hint": "neon",
    },
    "portfolio": {
        "label": "Personal Portfolio",
        "inject": """INDUSTRY DESIGN BRIEF: Personal Portfolio & Freelancer
VISUAL DIRECTION: The work is the hero. Clean, editorial, distinctive. Typography-forward.
TONE: Confident first-person. Direct and specific. Use the vocabulary of the person's exact craft.
SECTIONS TO PRIORITIZE: Immediate name + role hero with one-line positioning, curated portfolio grid, skills/services, brief about, testimonials from clients, contact/booking.
KEY CTAs: 'View My Work', 'Hire Me', 'Download Resume', 'Start a Conversation'
FONTS DIRECTION: Distinctive editorial pair. Fraunces or Playfair Display + Plus Jakarta Sans.
PALETTE DIRECTION: Clean white or warm off-white, one strong accent color that reflects personality.
BANNED WORDS: synergy, leverage, solutions, enterprise, comprehensive.""",
        "default_sections": ["hero", "portfolio", "services", "about", "testimonials", "contact"],
        "palette_hint": "soft",
    },
    "ecommerce": {
        "label": "E-commerce / Retail",
        "inject": """INDUSTRY DESIGN BRIEF: E-commerce & Retail Brand
VISUAL DIRECTION: Product-forward, editorial, lifestyle-feeling. NOT Amazon. More like Glossier or Aesop.
TONE: Direct DTC (direct-to-consumer) voice. Specific ingredients, materials, origin stories. Trust-building.
SECTIONS TO PRIORITIZE: Lifestyle hero (product-in-use), featured product grid in Services/Portfolio, brand story in About, customer reviews with star ratings + media in Testimonials, free shipping thresholds/returns in Contact/FAQ.
KEY CTAs: 'Shop Now', 'View Collection', 'Get 10% Off', 'Find Your Match'
FONTS DIRECTION: Clean modern sans. Outfit or Plus Jakarta Sans.
PALETTE DIRECTION: Matches the product aesthetic — warm cream for skincare, stark white for tech, earthy for sustainable goods.
BANNED WORDS: leverage, enterprise, scalable, API, deployment.""",
        "default_sections": ["hero", "portfolio", "about", "stats", "testimonials", "faq", "contact"],
        "palette_hint": "soft",
    },
    "consulting": {
        "label": "Consulting / Professional",
        "inject": """INDUSTRY DESIGN BRIEF: Consulting & Professional Services
VISUAL DIRECTION: Clean, intelligent, trustworthy. Premium but not flashy. Like McKinsey's editorial clarity.
TONE: Precise, evidence-based, senior. Use terms: operating model, organizational design, go-to-market, margin compression, first-principles, structural reform.
SECTIONS TO PRIORITIZE: Headline with results-first positioning, service verticals in Services, client outcomes in Stats (e.g. '120M revenue unlocked'), case studies in Portfolio, team credibility in About, contact for new mandates.
KEY CTAs: 'Start an Engagement', 'View Case Studies', 'Meet the Team', 'Request a Brief'
FONTS DIRECTION: Clean precision type. Outfit or Roboto. No decorative fonts.
PALETTE DIRECTION: Deep navy or slate bg headers, clean white content areas, gold or teal accent.
BANNED WORDS: fun, awesome, trendy, creative, cool, vibrant.""",
        "default_sections": ["hero", "services", "portfolio", "stats", "about", "testimonials", "contact"],
        "palette_hint": "midnight",
    },
    "realestate": {
        "label": "Real Estate / Property",
        "inject": """INDUSTRY DESIGN BRIEF: Real Estate & Property
VISUAL DIRECTION: Aspirational and premium. Architecture photography. Clean sophistication.
TONE: Trusted advisor voice. Use terms: prime location, yield, cap rate, square footage, bespoke finishes, master-planned, leasehold vs freehold, off-plan.
SECTIONS TO PRIORITIZE: Full-bleed architectural hero, listings/portfolio grid, agent/team in About, stats (properties sold, total value), client testimonials, valuation inquiry in Contact.
KEY CTAs: 'Book a Viewing', 'Get a Valuation', 'Browse Listings', 'Speak to an Agent'
FONTS DIRECTION: Elegant serif headings (Fraunces). Refined body font.
PALETTE DIRECTION: Warm white or cream bg, deep charcoal or slate primary, gold accent.
BANNED WORDS: cheap, discount, affordable, budget, scalable.""",
        "default_sections": ["hero", "portfolio", "about", "stats", "services", "testimonials", "contact"],
        "palette_hint": "luxury",
    },
}


async def generate_website_content(prompt, image_paths=None, image_count=0, industry=None):
    if not genai_client:
        print(">>> [ERROR] generate_website_content called but genai_client is None", flush=True)
        return None
    try:
        # The new SDK uses the client.models.generate_content interface
        # We use gemini-1.5-flash for stable, production-grade generation.
        fallback = get_fallback_tokens(prompt)
        layout   = get_layout_blueprint(prompt)

        # Explicitly tell the AI how many portfolio entries to make to match the images
        expected_port_count = min(3, max(0, image_count - 2))
        image_summary = ""
        if image_count > 0:
            image_summary = f"\nIMAGE ALLOCATION (MUST MATCH):\n- Image 1: Hero\n- Image 2: About"
            if image_count >= 3:
                image_summary += f"\n- Images 3 to {min(5, image_count)}: Portfolio Case Studies (You MUST generate exactly {expected_port_count} items in the 'portfolio' list)."

        # --- INDUSTRY TEMPLATE INJECTION ---
        # Silently build the full design brief like Stitch does
        industry_block = ""
        industry_template = INDUSTRY_TEMPLATES.get(industry) if industry else None
        if industry_template:
            industry_block = f"\n\n{industry_template['inject']}\n"
            # Override fallback sections with industry defaults if user didn't specify
            layout = industry_template["default_sections"]
            print(f">>> [INDUSTRY] Template '{industry}' injected.", flush=True)

        full_prompt = f"""Business: {prompt}{industry_block}
{image_summary}

DEVELOPER DESIGN TOKENS (pair these with Roboto for a premium look):
- font_heading: Roboto
- font_body: Roboto
- primary color: {fallback['primary']}
- bg: {fallback['bg']}
- bg_alt: {fallback['bg_alt']}
- text_main: {fallback['text_main']}
- text_muted: {fallback['text_muted']}
- accent: {fallback['accent']}
- mood: {fallback['mood']}

SUGGESTED LAYOUT: {layout}
IMAGES UPLOADED: {image_count}
{"Analyze uploaded images for color and mood." if image_count > 0 else "No images — generate theme from business type only."}

Write copy like a senior creative director for a high-end boutique agency. Roleplay as a human.
CRITICAL RULES for Copy:
1. NO AI-ISMS: Ban "Unlock", "Empower", "Comprehensive", "Seamless", "Journey", "Elevate".
2. PORTFOLIO MATCHING: You must generate EXACTLY {expected_port_count} portfolio items in the 'portfolio' list to match images 3, 4, and 5.
3. Content must be 100% realistic. If it's a law firm, sound like a top attorney. If it's a software agency, use specialized technical terms.
4. NO "Welcome to", "Experience the", "Discover the", "Our journey"."""

        content_parts = [full_prompt]
        if image_paths:
            for i, path in enumerate(image_paths):
                try:
                    img = Image.open(path)
                    # Label the image for the Vision model
                    label = "Hero Image" if i == 0 else "About Background" if i == 1 else f"Portfolio Project Image {i-1}"
                    content_parts.append(f"--- ATTACHED IMAGE {i+1} ({label}) ---")
                    content_parts.append(img)
                except Exception as e:
                    print(f">>> [IMG ERR] {path}: {str(e)}", flush=True)

        print(f">>> [AI START] Model: gemini-3.1-flash-image-preview | Images: {image_count}", flush=True)
        
        # Using asyncio.to_thread with the synchronous generate_content method
        # for better stability and to avoid "Event loop is closed" errors with gRPC.
        response = await asyncio.to_thread(
            genai_client.models.generate_content,
            model="gemini-3.1-flash-image-preview",
            contents=content_parts,
            config={
                "system_instruction": system_prompt,
                "temperature": 0.85, 
                "max_output_tokens": 4000
            }
        )

        if not response:
            print(">>> [AI ERR] Empty response object", flush=True)
            return None

        # Check for safety blocks or errors in response
        try:
            text = response.text.strip()
            print(f">>> [AI RAW] {text[:150]}...", flush=True)
        except ValueError as ve:
            print(f">>> [AI BLOCKED] Error: {ve}", flush=True)
            if hasattr(response, 'prompt_feedback'):
                print(f">>> [AI FEEDBACK] {response.prompt_feedback}", flush=True)
            return None

        # Cleaning Markdown if present
        text = re.sub(r'^```[a-z]*\n?', '', text, flags=re.MULTILINE)
        text = re.sub(r'\n?```$', '', text, flags=re.MULTILINE)
        text = text.strip()

        try:
            data = json.loads(text)
            print(">>> [AI JSON] Parsed successfully", flush=True)
        except json.JSONDecodeError as je:
            print(f">>> [AI JSON ERR] Text: {text}", flush=True)
            return None

        # Auto-fix theme values
        data["theme"] = validate_and_fix_theme(
            data.get("theme", {}), fallback, has_image=(image_count > 0)
        )

        return data

    except Exception as e:
        print(f">>> [AI CRITICAL] {type(e).__name__}: {str(e)}", flush=True)
        traceback.print_exc()
        return None


def build_image_map(image_context: list, layout: list) -> dict:
    """
    Production-Grade Priority-Based Image Assignment Engine.

    Priority Order (fixed, by visual impact):
    ──────────────────────────────────────────────────────────────
    1. hero          → Full split-layout (Image 1 — ALWAYS assigned)
    2. about         → Full-bleed background with overlay
    3. portfolio     → Multi-image grid (consumes up to 3 images)
    4. services      → Cinematic banner above service cards
    5. testimonials  → Darkened full-width background
    ──────────────────────────────────────────────────────────────
    ZERO image slots for: contact, stats, faq, pricing (intentional)

    Overflow Rule:
    • If images remain after all selected visual sections are filled,
      they are collected into 'overflow' and shown as a Photo Gallery
      strip on the Home page. Zero images are ever wasted.
    """
    # Step 1: Deep-clean — only accept real HTTP URLs
    clean_images = [
        img for img in image_context
        if img and isinstance(img, str) and img.startswith('http')
    ]
    num_imgs = len(clean_images)

    # Step 2: Initialize all section image slots
    mapping = {
        "hero":         None,   # 1 slot  — split layout visual
        "about":        None,   # 1 slot  — full-bleed background
        "portfolio":    [],     # 1-3 slots — image grid cards
        "services":     None,   # 1 slot  — atmosphere banner
        "testimonials": None,   # 1 slot  — dark background
        # ── NO SLOTS ── contact / stats / faq / pricing
        "overflow":     [],     # Safety net — extra images → Home gallery
    }

    if num_imgs == 0:
        return mapping

    # Step 3: Priority order — contact/stats/faq/pricing deliberately excluded
    VISUAL_PRIORITY = ["hero", "about", "portfolio", "services", "testimonials"]

    # Step 4: Build assignment queue from user's selected layout (hero always included)
    user_layout_set = set(layout)
    assignment_queue = []
    for section in VISUAL_PRIORITY:
        if section == "hero" or section in user_layout_set:
            assignment_queue.append(section)

    # Step 5: Assign images sequentially — zero duplicacy
    img_idx = 0

    for section in assignment_queue:
        if img_idx >= num_imgs:
            break

        if section == "portfolio":
            # Portfolio consumes up to 3 images for its grid
            remaining = clean_images[img_idx:img_idx + 3]
            if remaining:
                mapping["portfolio"] = remaining
                img_idx += len(remaining)
        else:
            # Single-slot sections (hero, about, services, testimonials)
            mapping[section] = clean_images[img_idx]
            img_idx += 1

    # Step 6: OVERFLOW SAFETY — any unassigned images go back to the Home page
    # These render as a professional horizontal photo gallery below the hero
    if img_idx < num_imgs:
        mapping["overflow"] = clean_images[img_idx:]

    # Step 7: Debug log
    log = {k: ("✓" if v else "—") for k, v in mapping.items() if k not in ("portfolio", "overflow")}
    print(
        f">>> [IMG MAP] {log} | portfolio={len(mapping['portfolio'])} | "
        f"overflow={len(mapping['overflow'])} | used={img_idx}/{num_imgs}",
        flush=True
    )

    return mapping


@app.route('/')
async def index():
    """Health check root route for staging and production."""
    return jsonify({
        "status": "online",
        "service": "Website AI API",
        "message": "Backend is running correctly."
    }), 200


@app.route('/generate', methods=['POST'])
@require_auth
@require_credits(int(WEBSITE_AI_CREDIT_COST) if WEBSITE_AI_CREDIT_COST else 1)
async def generate_website():
    """
    INTAKE ROUTE — Fast, non-blocking.
    1. Validates inputs
    2. Uploads binary files to R2 (gets stable public URLs)
    3. Enqueues the job to Redis with all serializable data
    4. Returns {job_id} immediately so the frontend can poll /job-status
    The actual AI generation runs in worker.py.
    """
    try:
        prompt        = request.form.get('prompt', '')
        logo_file     = request.files.get('logo')
        user_pages    = request.form.get('pages', '')
        user_palette  = request.form.get('palette', 'auto')
        user_industry = request.form.get('industry', '').strip()
        user_id       = getattr(g, 'user_id', '00000000-0000-0000-0000-000000000001')

        print(f">>> [GENERATE] New request | industry={user_industry} pages={user_pages}", flush=True)

        if any(kw in prompt.lower() for kw in ["upload image", "add images yourself", "generate image", "create image"]):
            return jsonify({"warning": "Please upload images manually. AI generates content, not images."}), 400

        files = request.files.getlist('images')

        if not prompt:
            return jsonify({'error': 'Please provide a business description'}), 400

        if len(files) > MAX_IMAGES:
            return jsonify({"error": f"Max {MAX_IMAGES} images allowed."}), 400

        # ── Create website ID and local folder ──
        website_id     = str(uuid.uuid4())
        website_folder = os.path.join(app.config['GENERATED_FOLDER'], website_id)
        os.makedirs(website_folder, exist_ok=True)
        print(f">>> [GENERATE] website_id={website_id}", flush=True)

        import io
        db_image_records = []
        logo_web_path    = None

        # ── Upload logo to R2 ──
        if logo_file and logo_file.filename and allowed_file(logo_file.filename):
            logo_filename    = secure_filename(logo_file.filename)
            unique_logo_name = f"logo_{uuid.uuid4()}_{logo_filename}"
            logo_filepath    = os.path.join(app.config['UPLOAD_FOLDER'], unique_logo_name)
            logo_file.save(logo_filepath)
            with open(logo_filepath, "rb") as f:
                logo_bytes = f.read()
            try:
                img = Image.open(io.BytesIO(logo_bytes))
                img.verify()
                img = Image.open(io.BytesIO(logo_bytes))
                l_width, l_height = img.size
                l_format = img.format or "UNKNOWN"
            except Exception as e:
                return jsonify({"error": f"Invalid logo image: {str(e)}"}), 400

            logo_web_path = await asyncio.to_thread(
                upload_media_to_r2, logo_bytes, logo_file.mimetype,
                folder=f"websites/{website_id}/assets"
            )
            print(f">>> [GENERATE] Logo uploaded to R2: {logo_web_path}", flush=True)
            # Clean up local temp file — the R2 copy is the permanent one
            try:
                os.remove(logo_filepath)
            except Exception:
                pass
            db_image_records.append({
                "file_url": logo_web_path, "file_name": logo_filename,
                "file_format": l_format, "file_size_mb": len(logo_bytes) / (1024 * 1024),
                "width": l_width, "height": l_height,
                "image_type": "logo", "is_generated": False
            })

        # ── Upload content images to R2 ──
        image_urls  = []   # R2 public URLs → go into the queue payload
        image_paths = []   # Local disk paths → used by Gemini Vision in worker

        for i, file in enumerate(files):
            if file and file.filename and allowed_file(file.filename):
                filename        = secure_filename(file.filename)
                unique_filename = f"{uuid.uuid4()}_{filename}"
                filepath        = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(filepath)
                with open(filepath, "rb") as f:
                    file_bytes = f.read()
                try:
                    img = Image.open(io.BytesIO(file_bytes))
                    img.verify()
                    img = Image.open(io.BytesIO(file_bytes))
                    i_width, i_height = img.size
                    i_format = img.format or "UNKNOWN"
                except Exception as e:
                    return jsonify({"error": f"Invalid image file: {filename}"}), 400

                web_path = await asyncio.to_thread(
                    upload_media_to_r2, file_bytes, file.mimetype,
                    folder=f"websites/{website_id}/assets"
                )
                image_urls.append(web_path)
                image_paths.append(filepath)
                print(f">>> [GENERATE] Image {i+1} uploaded: {web_path}", flush=True)

                img_type = "hero" if i == 0 else ("about" if i == 1 else "portfolio")
                db_image_records.append({
                    "file_url": web_path, "file_name": filename,
                    "file_format": i_format, "file_size_mb": len(file_bytes) / (1024*1024),
                    "width": i_width, "height": i_height,
                    "image_type": img_type, "is_generated": False
                })

        # ── Enqueue job to Redis (with graceful fallback if Redis is unavailable) ──
        redis_available = False
        try:
            from core.redis import enqueue_website_ai_job, redis as redis_client
            import redis.exceptions as redis_exc
            # Quick ping to check if Redis is reachable before attempting enqueue
            await redis_client.ping()
            redis_available = True
        except Exception as redis_check_err:
            import traceback
            print(f">>> [REDIS] Not available ({type(redis_check_err).__name__}): {redis_check_err}", flush=True)
            traceback.print_exc()
            print(">>> [REDIS] Falling back to synchronous generation...", flush=True)

        if redis_available:
            # ── QUEUE MODE: Fast return, worker processes async ──
            job_id = await enqueue_website_ai_job(
                website_id=website_id,
                user_id=str(user_id),
                prompt=prompt,
                image_urls=image_urls,
                image_paths=image_paths,
                logo_url=logo_web_path,
                user_pages=user_pages,
                user_palette=user_palette,
                user_industry=user_industry,
                db_image_records=db_image_records,
            )
            print(f">>> [GENERATE] Job enqueued | job_id={job_id} website_id={website_id}", flush=True)
            return jsonify({
                "success":    True,
                "job_id":     job_id,
                "website_id": website_id,
                "status":     "queued",
            })

        else:
            # ── SYNCHRONOUS FALLBACK MODE: Redis is down, run generation directly ──
            print(f">>> [GENERATE] Running synchronous fallback for website_id={website_id}", flush=True)

            data = await generate_website_content(
                prompt, image_paths, len(image_paths),
                industry=user_industry or None
            )
            if not data:
                return jsonify({"error": "AI failed to generate content. Please try again."}), 500

            # Clean up local temp files — Gemini Vision is done with them
            for _fp in image_paths:
                try:
                    os.remove(_fp)
                except Exception:
                    pass

            site_name  = data.get("site_info", {}).get("display_name", "My Business")
            site_title = data.get("site_info", {}).get("site_title", site_name)
            tagline    = data.get("site_info", {}).get("tagline", "")
            theme      = data.get("theme", {})
            footer     = data.get("footer", {})

            if user_pages:
                valid_sections = {"hero","about","services","portfolio","testimonials","stats","faq","pricing","contact"}
                requested = [s.strip() for s in user_pages.split(',') if s.strip() in valid_sections]
                layout = requested if requested else data.get("layout", ["hero","about","services","contact"])
            else:
                layout = data.get("layout", ["hero","about","services","contact"])

            if user_palette and user_palette != 'auto' and user_palette in PALETTE_MAP:
                theme.update(PALETTE_MAP[user_palette])
                print(f">>> [THEME] Palette '{user_palette}' applied.", flush=True)

            clean_image_urls = [u for u in image_urls if u]
            image_map = build_image_map(clean_image_urls, layout)

            base_ctx = dict(
                site_name=site_name, site_title=site_title,
                tagline=tagline, theme=theme, footer=footer,
                layout=layout, image_map=image_map,
                image_count=len(clean_image_urls),
                has_images=(len(clean_image_urls) > 0),
                logo=logo_web_path,
                services_img=image_map.get("services"),
                testimonials_img=image_map.get("testimonials"),
                overflow_imgs=image_map.get("overflow", []),
            )

            home_html = render_template(
                "home.html", **base_ctx,
                home=data.get("home", {}), about=data.get("about", {}),
                services=data.get("services", []), portfolio=data.get("portfolio", []),
                testimonials=data.get("testimonials", []), faq=data.get("faq", []),
                pricing=data.get("pricing", []), stats=data.get("stats", []),
                contact=data.get("contact", {}), images=clean_image_urls,
            )
            await asyncio.to_thread(
                upload_media_to_r2, home_html.encode("utf-8"), "text/html",
                folder=f"websites/{website_id}", filename="home.html"
            )
            print(f">>> [GENERATE] home.html uploaded to R2", flush=True)

            page_templates = {
                "about.html":     ("about.html",     dict(**base_ctx, about=data.get("about",{}), services=data.get("services",[]), images=clean_image_urls)),
                "services.html":  ("services.html",  dict(**base_ctx, services=data.get("services",[]), images=clean_image_urls)),
                "portfolio.html": ("portfolio.html", dict(**base_ctx, portfolio=data.get("portfolio",[]), images=clean_image_urls)),
                "contact.html":   ("contact.html",   dict(**base_ctx, contact=data.get("contact",{}), images=clean_image_urls)),
            }
            for out_name, (tmpl, ctx) in page_templates.items():
                try:
                    html = render_template(tmpl, **ctx)
                    await asyncio.to_thread(
                        upload_media_to_r2, html.encode("utf-8"), "text/html",
                        folder=f"websites/{website_id}", filename=out_name
                    )
                    print(f">>> [GENERATE] {out_name} uploaded to R2", flush=True)
                except Exception as page_err:
                    print(f">>> [GENERATE WARN] {out_name} failed: {page_err}", flush=True)

            # Backup
            try:
                await asyncio.to_thread(
                    upload_media_to_r2, home_html.encode("utf-8"), "text/html",
                    folder=f"websites/{website_id}", filename="home_backup.html"
                )
            except Exception:
                pass

            # DB persist (MongoDB)
            try:
                website_doc = {
                    "website_id": website_id,
                    "user_id": str(user_id),
                    "prompt": prompt,
                    "status": "completed",
                    "progress": "100",
                    "final_url": f"{R2_PUBLIC_URL}/websites/{website_id}/home.html",
                    "industry": user_industry,
                    "site_name": site_name,
                    "tagline": tagline,
                    "layout": list(layout),
                    "theme": dict(theme),
                    "footer": dict(footer) if isinstance(footer, dict) else footer,
                    "ai_data": data,
                    "chat_messages": []
                }
                await insert_website_data(website_doc, db_image_records)
                print(f">>> [DB] Saved website + {len(db_image_records)} images to MongoDB: {website_id}", flush=True)
            except Exception as db_err:
                print(f">>> [DB ERR] MongoDB save failed: {db_err}", flush=True)

            # In-memory context for chat-edit (only store if enabled to save memory)
            if ENABLE_CHAT_EDIT:
                WEBSITE_CONTEXTS[website_id] = {
                    "prompt": prompt, "industry": user_industry, "layout": list(layout),
                    "theme": dict(theme), "data": data, "image_context": clean_image_urls,
                    "image_map": dict(image_map), "logo": logo_web_path,
                    "site_name": site_name, "site_title": site_title,
                    "tagline": tagline, "footer": footer,
                }

            # ── CREDIT DEDUCTION ──
            if str(user_id) != '00000000-0000-0000-0000-000000000001':
                cost = int(WEBSITE_AI_CREDIT_COST) if WEBSITE_AI_CREDIT_COST else 1
                credit_result = await website_credits_debits({
                    "userId": str(user_id),
                    "resourceType": "website_generation",
                    "resourceId": website_id,
                    "type": "USAGE",
                    "amount": -cost,
                    "description": "Website Generation (Sync)"
                })
                if not credit_result.get("success"):
                    print(f">>> [CREDIT ERR] Failed to deduct credits for sync generation: {credit_result.get('error')}", flush=True)

            print(f">>> [GENERATE] Synchronous generation complete | website_id={website_id}", flush=True)

            return jsonify({
                "success":    True,
                "website_id": website_id,
                "status":     "completed",
            })

    except Exception as e:
        print(f">>> [GENERATE CRITICAL] {str(e)}", flush=True)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/job-status/<job_id>', methods=['GET'])
@require_auth
async def job_status(job_id: str):
    """
    Polling endpoint for the frontend.
    Returns the current status of a generation job.
    Possible statuses: queued | processing | completed | failed
    """
    try:
        from core.redis import get_job_status, mark_job_failed
        data = await get_job_status(job_id)
        if not data:
            return jsonify({"error": "Job not found"}), 404

        # If the job has been processing for too long, mark it as failed.
        status = data.get("status")
        timestamp = data.get("updated_at") or data.get("created_at")
        if status in ("queued", "processing") and timestamp:
            try:
                ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if datetime.utcnow() - ts > timedelta(minutes=8):
                    await mark_job_failed(job_id, "Generation timed out waiting for the worker.")
                    data["status"] = "failed"
                    data["error"] = "Generation timed out after 8 minutes. Please try again."
            except Exception:
                pass

        return jsonify(data)
    except Exception as e:
        print(f">>> [JOB STATUS ERR] {job_id}: {e}", flush=True)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/editor/<website_id>')
@require_auth
async def editor_page(website_id):
    token = request.args.get('token')
    job_id = request.args.get('job_id')

    if not job_id:
        try:
            from core.redis import get_job_id_for_website
            job_id = await get_job_id_for_website(website_id)
            if job_id:
                print(f">>> [EDITOR] Recovered job_id={job_id} for website_id={website_id}", flush=True)
        except Exception as job_lookup_err:
            print(f">>> [EDITOR] Could not recover job_id for {website_id}: {job_lookup_err}", flush=True)

    response = make_response(render_template('editor.html', website_id=website_id, chat_enabled=ENABLE_CHAT_EDIT))
    if token:
        from urllib.parse import urlencode

        clean_query = {}
        if job_id:
            clean_query["job_id"] = job_id
        clean_url = f"/editor/{website_id}"
        if clean_query:
            clean_url = f"{clean_url}?{urlencode(clean_query)}"

        response = make_response(redirect(clean_url))
        # Set session cookie for background API calls (/job-status, /preview)
        # Use path='/' so the cookie is available across all routes on this site.
        cookie_host = (request.host or "").split(":")[0]
        is_local_cookie = cookie_host in {"127.0.0.1", "localhost"}
        response.set_cookie(
            'auth_token',
            token,
            httponly=True,
            samesite='Lax' if is_local_cookie else 'None',
            secure=not is_local_cookie,
            path='/',
        )
        print(f">>> [AUTH] Session cookie set for editor: {website_id}; redirecting to clean URL", flush=True)
    elif job_id and not request.args.get('job_id'):
        return redirect(f"/editor/{website_id}?job_id={job_id}")
    return response


@app.route('/history', methods=['GET'])
@require_auth
async def history():
    """
    Returns the authenticated user's project history for the last 7 days.
    """
    try:
        user_uuid = pyuuid.UUID(str(getattr(g, 'user_id', '')))
    except Exception:
        return jsonify({"error": "Invalid user ID format."}), 400

    cutoff = datetime.utcnow() - timedelta(days=7)
    collection = get_websites_collection()

    query = {
        "user_id": str(user_uuid),
        "_id": {"$gte": ObjectId.from_datetime(cutoff)},
    }
    cursor = collection.find(query).sort("_id", -1)
    websites = await cursor.to_list(length=None)

    items = []
    for website in websites:
        website_id = str(website.get("website_id", website.get("_id")))
        created_at = website.get("created_at")
        if not created_at and website.get("_id"):
            created_at = website["_id"].generation_time

        updated_at = website.get("updated_at") or created_at
        images = website.get("images") or []
        chat_messages = website.get("chat_messages") or []

        items.append({
            "website_id": website_id,
            "site_name": website.get("site_name"),
            "industry": website.get("industry"),
            "prompt": website.get("prompt"),
            "status": website.get("status"),
            "progress": website.get("progress"),
            "final_url": website.get("final_url"),
            "preview_url": f"/preview/{website_id}/home.html",
            "download_url": f"/download/{website_id}",
            "created_at": created_at.isoformat() if created_at else None,
            "updated_at": updated_at.isoformat() if updated_at else None,
            "layout": website.get("layout") or [],
            "theme": website.get("theme") or {},
            "footer": website.get("footer") or {},
            "image_count": len(images),
            "chat_count": len(chat_messages),
        })

    return jsonify({
        "success": True,
        "range_days": 7,
        "count": len(items),
        "items": items,
    })

@app.route('/preview/<website_id>')
@app.route('/preview/<website_id>/')
@app.route('/preview/<website_id>/<path:filename>')
async def preview_proxy(website_id, filename='home.html'):
    print(f">>> [PREVIEW DEBUG] Request for {website_id} / {filename}", flush=True)
    try:
        # 1. NORMALIZE PATH
        clean_filename = (filename or 'home.html').strip('/')
        if not clean_filename or clean_filename == '.':
            clean_filename = 'home.html'

        object_key = f"websites/{website_id}/{clean_filename}"
        html_bytes = None

        # Helper: safely fetch from R2, returns None instead of raising
        def _try_fetch(key):
            try:
                return fetch_media_from_r2(key)
            except Exception:
                return None

        # 2. BUILD SEARCH QUEUE
        search_keys = [object_key]
        if clean_filename in ('home.html', 'index.html'):
            alt = 'index.html' if clean_filename == 'home.html' else 'home.html'
            search_keys.append(f"websites/{website_id}/{alt}")
        if not clean_filename.endswith('.html'):
            search_keys.append(f"{object_key}.html")
        else:
            search_keys.append(object_key.rsplit('.html', 1)[0])

        # 3. TWO-PASS SEARCH (with R2 propagation wait between passes)
        for attempt in range(2):
            for key in search_keys:
                html_bytes = await asyncio.to_thread(_try_fetch, key)
                if html_bytes:
                    try:
                        html_text = html_bytes.decode('utf-8')
                    except Exception:
                        html_text = None

                    if html_text:
                        lower_text = html_text.lower()
                        invalid_preview = any(
                            marker in lower_text
                            for marker in [
                                '<title>404 not found',
                                'still generating',
                                'generation may have failed',
                                'your site is being built',
                            ]
                        )
                        if invalid_preview:
                            print(f">>> [PREVIEW INVALID] Ignoring placeholder/404 HTML for {key}", flush=True)
                            html_bytes = None
                        else:
                            if key != object_key or attempt > 0:
                                print(f">>> [PREVIEW OK] Resolved on attempt {attempt+1}: {key}", flush=True)
                            break
                    else:
                        print(f">>> [PREVIEW WARN] Unable to decode HTML bytes for {key}", flush=True)
                        html_bytes = None
            if html_bytes:
                break
            if attempt == 0:
                print(f">>> [PREVIEW WAIT] Not found, waiting 2s for R2... ({object_key})", flush=True)
                await asyncio.sleep(2.0)

        # 4. ABSOLUTE RECOVERY: list the bucket to find any home-page variant
        if not html_bytes:
            print(f">>> [PREVIEW RECOVERY] Bucket scan for {website_id}...", flush=True)
            try:
                from core.r2 import r2_client, R2_BUCKET_NAME  # correct export names
                response = await asyncio.to_thread(
                    r2_client.list_objects_v2,
                    Bucket=R2_BUCKET_NAME,
                    Prefix=f"websites/{website_id}/"
                )
                for obj in (response.get('Contents') or []):
                    key = obj['Key']
                    if any(x in key.lower() for x in ['home.html', 'index.html']):
                        print(f">>> [PREVIEW RECOVERY] Found: {key}", flush=True)
                        html_bytes = await asyncio.to_thread(_try_fetch, key)
                        if html_bytes:
                            try:
                                html_text = html_bytes.decode('utf-8')
                                if '<title>404 not found' in html_text.lower() or 'still generating' in html_text.lower():
                                    print(f">>> [PREVIEW RECOVERY INVALID] Ignoring placeholder/404 HTML for {key}", flush=True)
                                    html_bytes = None
                                else:
                                    break
                            except Exception:
                                print(f">>> [PREVIEW RECOVERY WARN] Failed to decode {key}", flush=True)
                                html_bytes = None
            except Exception as scan_err:
                print(f">>> [PREVIEW RECOVERY ERR] {scan_err}", flush=True)

        # 5. STILL NOTHING — return auto-refresh page with retry limit (max 10 attempts)
        if not html_bytes:
            print(f">>> [PREVIEW 404] All attempts exhausted: {object_key}", flush=True)
            return (
                "<html><body style='font-family:sans-serif;padding:40px;color:#555'>"
                "<h2 id='msg'>\u23f3 Still generating...</h2>"
                "<p id='sub'>Your site is being built. Retrying automatically...</p>"
                "<button id='btn' onclick='location.reload()' style='display:none;margin-top:16px;"
                "padding:10px 24px;background:#6366f1;color:#fff;border:none;border-radius:8px;"
                "font-size:1rem;cursor:pointer;'>Refresh Now</button>"
                "<script>"
                "var key='_pgRetry_'+location.pathname;"
                "var n=parseInt(localStorage.getItem(key)||'0')+1;"
                "localStorage.setItem(key,n);"
                "if(n<=10){"
                "  document.getElementById('sub').textContent='Attempt '+n+' of 10 — retrying in 3s...';"
                "  setTimeout(()=>location.reload(),3000);"
                "}else{"
                "  localStorage.removeItem(key);"
                "  document.getElementById('msg').textContent='\u26a0\ufe0f Generation may have failed';"
                "  document.getElementById('sub').textContent='Still generating\u2026 please refresh manually.';"
                "  document.getElementById('btn').style.display='inline-block';"
                "}"
                "</script>"
                "</body></html>",
                200,
                {"Content-Type": "text/html; charset=utf-8"},
            )

        html = html_bytes.decode("utf-8")

        # ── Strip editor-only UI elements ──────────────────────────────
        html = re.sub(r'<div\s+id=["\']edit-toolbar["\'][\s\S]*?</div>(\s*</div>)?', '', html, flags=re.I)
        html = re.sub(r'<div\s+id=["\']save-bar["\'][\s\S]*?</div>',                '', html, flags=re.I)
        html = re.sub(r'<div\s+id=["\']edit-hint-bar["\'][\s\S]*?</div>',           '', html, flags=re.I)
        html = re.sub(r'\s*contenteditable=["\']?(true|false)?["\']?',              '', html, flags=re.I)

        # ── Rewrite inter-page hrefs to absolute preview paths ─────────
        # This is the critical fix for navigation.  Without this, clicking
        # "Home" from contact.html resolves to a relative URL that may 404.
        #
        # Strategy: replace  href="<page>.html"  and  href='<page>.html'
        # (with optional #anchor suffix) → /preview/<id>/<page>.html[#anchor]
        # We handle all five named pages plus any .html file that lives under
        # this website's folder.
        BASE = f"/preview/{website_id}"
        NAMED_PAGES = ["home", "about", "services", "portfolio", "contact"]
        page_pattern = "|".join(re.escape(p) for p in NAMED_PAGES)

        # ── ROOT CAUSE FIX: Logo href rewrite ────────────────────────────────
        # The logo in base.html is:  <a href="home.html" class="nav-logo ...">
        # The old regex had 4 groups where group 4 expected a closing quote
        # IMMEDIATELY after the anchor. But the logo has class="..." after it,
        # so "home.html" was followed by a SPACE, not a quote — the regex never
        # matched, the href stayed as "home.html" (relative), and the browser
        # resolved it wrong inside the iframe → 404.
        #
        # Fix: Use 5 groups: (href=)(quote)(page)(anchor)(close-quote)
        # The close-quote MUST match the open-quote. This works because
        # href="home.html" the value ends at the closing quote, regardless
        # of what attributes come after it in the tag.
        html = re.sub(
            rf'(href=)(["\'])({page_pattern})\.html(#[^"\']*)?(["\'])',
            lambda m: (
                f'{m.group(1)}{m.group(2)}'
                f'{BASE}/{m.group(3)}.html'
                f'{m.group(4) or ""}'
                f'{m.group(5)}'
            ),
            html,
            flags=re.IGNORECASE,
        )

        return html, 200, {"Content-Type": "text/html; charset=utf-8"}

    except Exception as e:
        print(f">>> [PREVIEW ERR] {e}", flush=True)
        traceback.print_exc()
        return "Internal Proxy Error", 500


def clean_editor_artifacts(html: str) -> str:
    """Remove editor-only markup before persisting or exporting a page."""
    if not html:
        return html

    html = re.sub(r'<div\s+id=["\']edit-toolbar["\'][\s\S]*?</div>\s*</div>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'<div\s+id=["\']save-bar["\'][\s\S]*?</div>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'<div\s+id=["\']save-indicator["\'][\s\S]*?</div>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'<div\s+id=["\']edit-hint-bar["\'][\s\S]*?</div>', '', html, flags=re.IGNORECASE)

    # The editor injects a helper style tag into the iframe; it should never be
    # saved back to R2 or included in downloads/deployments.
    html = re.sub(
        r'<style[^>]*>\s*/\*\s*Hide legacy editor UI[\s\S]*?\.eb-img-edit:hover[\s\S]*?</style>',
        '',
        html,
        flags=re.IGNORECASE,
    )

    html = re.sub(r'\s*contenteditable=(["\'])?true\1?', '', html, flags=re.IGNORECASE)
    html = re.sub(r'\s*data-[a-zA-Z0-9_\-]+=(["\']).*?\1', '', html)

    def _clean_eb(m):
        sp = m.group(1)
        kept = [c for c in m.group(2).split() if not c.startswith("eb-")]
        return f'{sp}class="{" ".join(kept)}"' if kept else sp

    html = re.sub(r'(\s*)class="([^"]*\beb-[^"]*)"', _clean_eb, html)
    html = re.sub(r"(\s*)class='([^']*\beb-[^']*)'", _clean_eb, html)

    html = re.sub(r'<script[^>]*sortablejs[^>]*>\s*</script>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'new\s+Sortable\([^,]+,\s*\{[\s\S]*?\}\s*\)\s*;', '', html, flags=re.IGNORECASE)
    html = html.replace('initImageDropZones();', '')
    html = html.replace('showSaveIndicator();', '')
    return html


@app.route('/save', methods=['POST'])
@require_auth
async def save_html():
    try:
        data = request.get_json(silent=True) or {}
        website_id = data.get('website_id')
        html = data.get('html')
        page_name = os.path.basename(data.get('page_name') or 'home.html')
        
        if not website_id or not html:
            return jsonify({"error": "Missing data"}), 400

        try:
            pyuuid.UUID(str(website_id))
        except Exception:
            return jsonify({"error": "Invalid website ID format."}), 400

        if not page_name or not page_name.lower().endswith(".html"):
            return jsonify({"error": "Invalid page name."}), 400

        def is_invalid_html(content: str) -> bool:
            lowered = content.lower()
            invalid_markers = [
                '<title>404 not found',
                'still generating',
                'generation may have failed',
                'your site is being built',
            ]
            return any(marker in lowered for marker in invalid_markers)

        if is_invalid_html(html):
            print(f">>> [SAVE REJECTED] Invalid HTML content for {website_id}/{page_name}", flush=True)
            return jsonify({"error": "Invalid page content. Save aborted."}), 400

        html = clean_editor_artifacts(html)
            
        # Overwrite the specific page in R2 instantly
        await asyncio.to_thread(
            upload_media_to_r2,
            html.encode('utf-8'), 
            "text/html",
            folder=f"websites/{website_id}",
            filename=page_name
        )
        return jsonify({"success": True})
    except Exception as e:
        print(f">>> [SAVE ERR] {e}", flush=True)
        return jsonify({"error": str(e)}), 500

@app.route('/upload-image', methods=['POST'])
@require_auth
async def editor_upload_image():
    app.logger.info('POST /upload-image triggered')
    try:
        file = request.files.get('image')
        website_id = request.form.get('website_id')
        old_url = request.form.get('old_url', '')
        app.logger.info('Upload request: file=%s website_id=%s', file.filename if file else 'None', website_id)
        if not file or not website_id:
            return jsonify({"error": "Missing file or website_id"}), 400

        try:
            pyuuid.UUID(str(website_id))
        except Exception:
            return jsonify({"error": "Invalid website ID format."}), 400
            
        file_bytes = file.read()
        filename = None
        base_url = (R2_PUBLIC_URL or "").rstrip("/")
        if old_url and base_url and old_url.startswith(base_url):
            old_key = old_url[len(base_url):].lstrip("/").split("?", 1)[0]
            expected_prefix = f"websites/{website_id}/assets/"
            if old_key.startswith(expected_prefix):
                filename = os.path.basename(old_key)

        url = upload_media_to_r2(
            file_bytes,
            file.mimetype,
            folder=f"websites/{website_id}/assets",
            filename=filename,
        )
        return jsonify({"url": url})
    except Exception as e:
        print(f">>> [UPLOAD ERR] {e}", flush=True)
        return jsonify({"error": str(e)}), 500

@app.route('/download/<website_id>')
@require_auth
async def download_from_r2(website_id):
    """
    Production-grade website download endpoint.

    Produces a fully self-contained ZIP that works 100% offline:
      1. Fetches every HTML page from R2.
      2. Scans HTML for ALL R2-hosted image URLs.
      3. Downloads each image from R2, compresses it (WebP, max 1920px wide,
         quality 82) to keep the ZIP size manageable.
      4. Stores images at  assets/<filename>.webp  inside the ZIP.
      5. Rewrites every R2 URL in the HTML to the matching relative path.
      6. Strips all editor artefacts (toolbars, data-* attrs, eb-* classes).
      7. Re-wires navigation so all inter-page links work offline.

    Edge-cases handled:
      • Image already fetched (deduplication via asset_map dict).
      • Image fetch failure (skipped, original URL kept so the page still loads
        via CDN if online).
      • Corrupt / non-image bytes from R2 (caught, original URL preserved).
      • R2_PUBLIC_URL trailing slash variants.
      • Logo + portfolio + hero + about images all captured by a single regex.
      • Pages not in R2 (optional sections) silently skipped.
      • DB row missing (falls back to a sensible default page list).
    """
    import io
    import zipfile
    import posixpath
    import urllib.parse

    # Normalise the base URL so we can build object keys from full URLs
    base_url = (R2_PUBLIC_URL or "").rstrip("/")

    # ------------------------------------------------------------------
    # Helper — compress image bytes with Pillow
    # ------------------------------------------------------------------
    def compress_image(raw_bytes: bytes, max_width: int = 1920, quality: int = 82) -> bytes:
        """
        Convert any image to WebP, scale down if wider than max_width.
        Returns compressed bytes, or original raw_bytes if anything fails.
        """
        try:
            img = Image.open(io.BytesIO(raw_bytes))
            # Drop alpha for JPEG-like formats
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGBA")
            else:
                img = img.convert("RGB")
            # Downscale only if needed
            if img.width > max_width:
                ratio = max_width / img.width
                img = img.resize(
                    (max_width, int(img.height * ratio)),
                    Image.LANCZOS
                )
            out = io.BytesIO()
            img.save(out, format="WEBP", quality=quality, method=4)
            return out.getvalue()
        except Exception as compress_err:
            print(f">>> [ZIP IMG COMPRESS WARN] {compress_err}", flush=True)
            return raw_bytes  # fall back to original bytes

    # ------------------------------------------------------------------
    # ── VALIDATION ──
    try:
        w_uuid = pyuuid.UUID(website_id)
    except Exception:
        return jsonify({"error": "Invalid website ID format."}), 400

    # Helper — extract object_key from a full R2 URL
    def url_to_object_key(url: str) -> str | None:
        """
        Given  https://pub.r2.dev/websites/<id>/assets/hero.jpg
        returns              websites/<id>/assets/hero.jpg
        """
        if not base_url:
            return None
        url = url.strip()
        if url.startswith(base_url):
            return url[len(base_url):].lstrip("/").split("?", 1)[0]
        # Handle URL-encoded variants
        decoded = urllib.parse.unquote(url)
        if decoded.startswith(base_url):
            return decoded[len(base_url):].lstrip("/").split("?", 1)[0]
        return None

    try:
        # ── 1. Resolve page list from DB ──────────────────────────────
        try:
            saved_layout = await get_website_layout(website_id)
        except RuntimeError as e:
            if "Event loop is closed" in str(e):
                print(f">>> [DOWNLOAD] Event loop closed, using default layout for {website_id}", flush=True)
                saved_layout = []
            else:
                raise

        section_pages = {"about", "services", "portfolio", "contact"}
        pages_to_zip = ["home"]
        if saved_layout:
            generated_pages = [
                str(section).strip().lower().removesuffix(".html")
                for section in saved_layout
            ]
            pages_to_zip.extend(page for page in generated_pages if page in section_pages)
        pages_to_zip = list(dict.fromkeys(pages_to_zip))  # preserve order, deduplicate

        # ── 2. Collect all HTML pages from R2 ─────────────────────────
        # Map:  page_slug  →  (zip_entry_name, raw_html_string)
        # Keep the existing layout-driven flow, but fall back to real R2 files
        # when the saved layout is stale and a page was generated anyway.
        from core.r2 import r2_client, R2_BUCKET_NAME

        section_order = ("about", "services", "portfolio", "contact")

        def page_exists_in_r2(page_slug: str) -> bool:
            object_key = f"websites/{website_id}/{page_slug}.html"
            try:
                r2_client.head_object(Bucket=R2_BUCKET_NAME, Key=object_key)
                return True
            except Exception:
                return False

        for page in section_order:
            if page not in pages_to_zip and page_exists_in_r2(page):
                pages_to_zip.append(page)

        page_htmls: dict[str, tuple[str, str]] = {}
        for page in pages_to_zip:
            filename   = f"{page}.html"
            object_key = f"websites/{website_id}/{filename}"
            try:
                html_bytes = await asyncio.to_thread(fetch_media_from_r2, object_key)
                if not html_bytes:
                    continue
                zip_name = "index.html" if page == "home" else filename
                page_htmls[page] = (zip_name, html_bytes.decode("utf-8"))
                print(f">>> [ZIP] Fetched from R2: {filename}", flush=True)
            except Exception as fetch_err:
                print(f">>> [ZIP SKIP] {page}: {fetch_err}", flush=True)

        if not page_htmls:
            return jsonify({"error": "No website pages found for this ID."}), 404

        missing_pages = [f"{p}.html" for p in pages_to_zip if p not in page_htmls]

        # ── 3. Discover ALL R2 image URLs across every page ───────────
        # Matches  src="..."  and  url("...")  or  url('...')  in inline CSS
        IMG_SRC_PATTERN = re.compile(
            r'(?:src=["\']|url\(["\']?)(' + re.escape(base_url) + r'/[^"\')\s>]+)',
            re.IGNORECASE
        )

        # asset_map:  original_r2_url  →  relative path inside ZIP  (e.g. "assets/hero.webp")
        asset_map: dict[str, str] = {}
        # asset_bytes:  relative path  →  compressed bytes
        asset_bytes_map: dict[str, bytes] = {}

        all_html_combined = "\n".join(html for _, html in page_htmls.values())
        seen_urls = set(IMG_SRC_PATTERN.findall(all_html_combined))

        print(f">>> [ZIP] Discovered {len(seen_urls)} unique R2 asset URLs", flush=True)

        # ── 4. Download + compress each image ─────────────────────────
        for r2_url in seen_urls:
            object_key = url_to_object_key(r2_url)
            if not object_key:
                continue

            # Derive a safe local filename (preserving the original extension
            # so we can detect non-images quickly, but output is always .webp)
            original_filename = posixpath.basename(object_key)
            # Strip query strings if any
            original_filename = original_filename.split("?")[0]
            stem = posixpath.splitext(original_filename)[0]
            local_name = f"assets/{stem}.webp"

            # Skip formats that clearly aren't raster images (SVG, fonts, etc.)
            lower_key = object_key.lower()
            if any(lower_key.endswith(ext) for ext in (".svg", ".woff", ".woff2", ".ttf", ".eot", ".otf", ".mp4", ".webm")):
                # Keep as-is — copy the original bytes without compression
                try:
                    raw = await asyncio.to_thread(fetch_media_from_r2, object_key)
                    if raw:
                        no_compress_name = f"assets/{original_filename}"
                        asset_map[r2_url]             = no_compress_name
                        asset_bytes_map[no_compress_name] = raw
                        print(f">>> [ZIP] Asset (no-compress): {no_compress_name}", flush=True)
                except Exception as nc_err:
                    print(f">>> [ZIP ASSET SKIP] {object_key}: {nc_err}", flush=True)
                continue

            # Raster image — download and compress
            try:
                raw = await asyncio.to_thread(fetch_media_from_r2, object_key)
                if not raw:
                    print(f">>> [ZIP ASSET EMPTY] {object_key}", flush=True)
                    continue
                compressed = await asyncio.to_thread(compress_image, raw)
                asset_map[r2_url]         = local_name
                asset_bytes_map[local_name] = compressed
                saving_pct = round((1 - len(compressed) / len(raw)) * 100) if len(raw) else 0
                print(
                    f">>> [ZIP] Asset compressed: {local_name} "
                    f"({len(raw)//1024}KB → {len(compressed)//1024}KB, -{saving_pct}%)",
                    flush=True
                )
            except Exception as img_err:
                # Non-fatal: leave original URL in HTML so it still works online
                print(f">>> [ZIP ASSET FAIL] {object_key}: {img_err}", flush=True)

        # ── 5. Build the ZIP ──────────────────────────────────────────
        memory_zip = io.BytesIO()
        with zipfile.ZipFile(memory_zip, "w", zipfile.ZIP_DEFLATED) as zf:

            # 5a. Write assets
            for local_path, data in asset_bytes_map.items():
                zf.writestr(local_path, data)

            # 5b. Process and write each HTML page
            for page, (zip_name, html) in page_htmls.items():
                html = clean_editor_artifacts(html)

                # ── CLEANING ──
                # Remove editor UI components
                html = re.sub(r'<div\s+id="edit-toolbar"[\s\S]*?</div>\s*</div>', '', html)
                html = re.sub(r'<div\s+id="save-bar"[\s\S]*?</div>',             '', html)
                html = re.sub(r'<div\s+id="save-indicator"[\s\S]*?</div>',        '', html)
                html = re.sub(r'<div\s+id="edit-hint-bar"[\s\S]*?</div>',         '', html)

                # Strip editor markers and data attributes
                html = re.sub(r'\s*contenteditable="true"',          '', html)
                html = re.sub(r'\s*data-[a-zA-Z0-9_\-]+="[^"]*"',   '', html)

                # --- Dead Link Auto-Scrubber ---
                for missing in missing_pages:
                    html = re.sub(rf'<a[^>]*href=["\']{missing}(#[^"\']*)?["\'][^>]*>.*?</a>', '', html, flags=re.IGNORECASE)
                # -------------------------------

                # Remove ONLY eb-* classes, keep all structural classes intact
                def _clean_eb(m):
                    sp     = m.group(1)
                    kept   = [c for c in m.group(2).split() if not c.startswith("eb-")]
                    return f'{sp}class="{" ".join(kept)}"' if kept else sp

                html = re.sub(r'(\s*)class="([^"]*\beb-[^"]*)"', _clean_eb, html)

                # ── Strip ALL editor-only JavaScript ────────────────────────
                # 1. Remove SortableJS CDN <script> tag
                html = re.sub(
                    r'<script[^>]*sortablejs[^>]*>\s*</script>',
                    '', html, flags=re.IGNORECASE
                )

                # 2. Remove the `new Sortable(...)` call block.
                #    This block lives inside a DOMContentLoaded listener and
                #    calls Sortable which no longer exists — causing a ReferenceError.
                #    We strip it out safely without breaking {}, leaving the wrapper intact.
                html = re.sub(r'new\s+Sortable\([^,]+,\s*\{[\s\S]*?\}\s*\)\s*;', '', html, flags=re.IGNORECASE)

                # 3. Remove initImageDropZones() call (editor-only function)
                html = html.replace('initImageDropZones();', '')

                # 5. Remove showSaveIndicator() calls (editor-only function)
                html = html.replace('showSaveIndicator();', '')

                # ── URL REWRITING ──
                # Replace every known R2 image URL with its local relative path
                for r2_url, local_rel in asset_map.items():
                    # Escape for use inside regex  (URLs may contain dots, slashes etc.)
                    html = html.replace(r2_url, local_rel)

                # Strip any remaining absolute preview paths → relative
                html = re.sub(fr'/preview/{re.escape(website_id)}/', '', html)

                # ── NAVIGATION RE-WIRING ────────────────────────────────────
                # home.html must become index.html everywhere — this covers:
                #   • href="home.html"          (double quote, nav link)
                #   • href='home.html'          (single quote, Jinja output)
                #   • href="home.html#stats"    (with anchor, sections like faq/stats/pricing)
                #   • href='home.html#testimonials'
                #   • Any occurrence in footer links, logo hrefs, etc.
                html = re.sub(
                    r'href=(["\'])home\.html(#[^"\']*)?(["\'])',
                    lambda m: f'href={m.group(1)}index.html{m.group(2) or ""}{m.group(1)}',
                    html,
                    flags=re.IGNORECASE,
                )

                # Also rewrite all other page links to match what's actually in the ZIP
                # For pages NOT included, convert links to plain text to prevent 404s
                for other_page in section_pages:
                    if other_page not in page_htmls:
                        # Remove the link tags but preserve text content
                        # Match both single and double quoted hrefs to missing pages
                        missing_link_pattern = '<a[^>]*?href=["\']?' + re.escape(other_page) + r'\.html[^>]*?>(.*?)</a>'
                        html = re.sub(missing_link_pattern, r'\1', html, flags=re.IGNORECASE | re.DOTALL)

                zf.writestr(zip_name, html)
                print(f">>> [ZIP] Written: {zip_name}", flush=True)

        # ── 6. Stream the ZIP to the client ──────────────────────────
        memory_zip.seek(0)
        total_kb = memory_zip.getbuffer().nbytes // 1024
        print(f">>> [ZIP] Complete — {total_kb} KB | assets={len(asset_bytes_map)} | pages={len(page_htmls)}", flush=True)
        return send_file(
            memory_zip,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"website_{website_id}.zip",
        )

    except Exception as e:
        print(f">>> [DOWNLOAD CRITICAL] {e}", flush=True)
        traceback.print_exc()
        return jsonify({"error": "Failed to prepare download. Please try again."}), 500


@app.route('/deploy/<website_id>', methods=['GET', 'POST'])
@require_auth
async def deploy_to_vercel(website_id):
    """
    Deploys the generated site directly to Vercel via CLI.
    Creates a temporary folder, writes all HTML and assets,
    injects vercel.json, then runs the Vercel CLI to deploy automatically.
    """
    import io
    import os
    import re
    import json
    import tempfile
    import shutil
    import asyncio
    import traceback
    import urllib.parse
    import uuid
    from sqlalchemy import select

    # ── VALIDATION ──
    try:
        w_uuid = pyuuid.UUID(website_id)
    except Exception:
        return jsonify({"error": "Invalid website ID format."}), 400

    vercel_token = os.getenv("VERCEL_TOKEN")
    if not vercel_token:
        return jsonify({"error": "VERCEL_TOKEN is not configured on the server."}), 500

    base_url = (R2_PUBLIC_URL or "").rstrip("/")

    # Compress helper
    def compress_image(raw_bytes: bytes, max_width: int = 1920, quality: int = 82) -> bytes:
        try:
            img = Image.open(io.BytesIO(raw_bytes))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGBA")
            else:
                img = img.convert("RGB")
            if img.width > max_width:
                ratio = max_width / img.width
                img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)
            out = io.BytesIO()
            img.save(out, format="WEBP", quality=quality, method=4)
            return out.getvalue()
        except Exception:
            return raw_bytes

    def url_to_object_key(url: str) -> str | None:
        if not base_url: return None
        url = url.strip()
        if url.startswith(base_url): return url[len(base_url):].lstrip("/").split("?", 1)[0]
        decoded = urllib.parse.unquote(url)
        if decoded.startswith(base_url): return decoded[len(base_url):].lstrip("/").split("?", 1)[0]
        return None

    tmp_dir = tempfile.mkdtemp(prefix=f"deploy_{website_id[:8]}_")
    try:
        # 1. Resolve page list
        saved_layout = await get_website_layout(website_id)

        pages_to_build = ["home", "about", "services", "portfolio", "contact"]
        if saved_layout:
            pages_to_build = list(dict.fromkeys(pages_to_build + saved_layout))

        # 2. Collect HTML
        page_htmls = {}
        for page in pages_to_build:
            filename   = f"{page}.html"
            object_key = f"websites/{website_id}/{filename}"
            try:
                html_bytes = await asyncio.to_thread(fetch_media_from_r2, object_key)
                if not html_bytes: continue
                out_name = "index.html" if page == "home" else filename
                page_htmls[page] = (out_name, html_bytes.decode("utf-8"))
            except Exception:
                pass

        if not page_htmls:
            raise Exception("No website pages found for deployment.")

        missing_pages = [f"{p}.html" for p in pages_to_build if p not in page_htmls]

        # 3. Discover Images
        IMG_SRC_PATTERN = re.compile(r'(?:src=["\']|url\(["\']?)(' + re.escape(base_url) + r'/[^"\')\s>]+)', re.IGNORECASE)
        all_r2_urls = set()
        for _, (_, html) in page_htmls.items():
            matches = IMG_SRC_PATTERN.findall(html)
            all_r2_urls.update(matches)

        # 4. Fetch & Compress Images
        asset_map, asset_bytes_map = {}, {}
        for r2_url in all_r2_urls:
            object_key = url_to_object_key(r2_url)
            if not object_key: continue
            original_filename = object_key.split('/')[-1].split('?')[0]
            lower_key = object_key.lower()
            
            try:
                raw = await asyncio.to_thread(fetch_media_from_r2, object_key)
                if not raw: continue
                if any(lower_key.endswith(ext) for ext in (".svg", ".woff", ".woff2", ".ttf", ".eot", ".otf", ".mp4", ".webm")):
                    local_name = f"assets/{original_filename}"
                    asset_map[r2_url] = local_name
                    asset_bytes_map[local_name] = raw
                else:
                    local_name = f"assets/{original_filename}"
                    if not local_name.lower().endswith('.webp'):
                        local_name = local_name.rsplit('.', 1)[0] + ".webp"
                    compressed = await asyncio.to_thread(compress_image, raw)
                    asset_map[r2_url] = local_name
                    asset_bytes_map[local_name] = compressed
            except Exception:
                pass

        # 5. Build into Temp Folder
        assets_dir = os.path.join(tmp_dir, "assets")
        os.makedirs(assets_dir, exist_ok=True)
        
        for local_path, data in asset_bytes_map.items():
            with open(os.path.join(tmp_dir, local_path), 'wb') as f:
                f.write(data)

        for page, (out_name, html) in page_htmls.items():
            html = clean_editor_artifacts(html)
            # Clean HTML exactly like download route
            html = re.sub(r'<div\s+id="edit-toolbar"[\s\S]*?</div>\s*</div>', '', html)
            html = re.sub(r'<div\s+id="save-bar"[\s\S]*?</div>', '', html)

            # --- Dead Link Auto-Scrubber ---
            for missing in missing_pages:
                html = re.sub(rf'<a[^>]*href=["\']{missing}(#[^"\']*)?["\'][^>]*>.*?</a>', '', html, flags=re.IGNORECASE)
            # -------------------------------
            html = re.sub(r'<div\s+id="save-indicator"[\s\S]*?</div>', '', html)
            html = re.sub(r'<div\s+id="edit-hint-bar"[\s\S]*?</div>', '', html)
            html = re.sub(r'\s*contenteditable="true"', '', html)
            html = re.sub(r'\s*data-[a-zA-Z0-9_\-]+="[^"]*"', '', html)

            def _clean_eb(m):
                sp = m.group(1)
                kept = [c for c in m.group(2).split() if not c.startswith("eb-")]
                return f'{sp}class="{" ".join(kept)}"' if kept else sp

            html = re.sub(r'(\s*)class="([^"]*\beb-[^"]*)"', _clean_eb, html)
            
            # --- BACKWARDS COMPATIBILITY FIXES FOR OLD SITES ---
            # Remove legacy white-wash gradient
            html = html.replace('linear-gradient(rgba(255,255,255,0.92), rgba(255,255,255,0.92)), ', '')
            html = html.replace('linear-gradient(var(--nav-bg), var(--nav-bg)), ', '')
            
            # Dynamically inject the new cinematic overlay style to old about-landing / about-sections
            if '<style' in html:
                html = html.replace('</style>', 
                '''    .about-landing, .about-section { position: relative; overflow: hidden; }
    .about-landing::before, .about-section::before {
        content: ''; position: absolute; inset: 0; pointer-events: none;
        background: linear-gradient(135deg, rgba(0,0,0,0.82) 0%, rgba(0,0,0,0.55) 50%, rgba(0,0,0,0.72) 100%);
        z-index: 1;
    }
    .about-landing, .about-landing *, .about-section, .about-section * { color: #fff !important; }
    .about-grid { position: relative; z-index: 2; }
</style>''')
            # ---------------------------------------------------

            html = re.sub(r'<script[^>]*sortablejs[^>]*>\s*</script>', '', html, flags=re.IGNORECASE)
            html = re.sub(r'new\s+Sortable\([^,]+,\s*\{[\s\S]*?\}\s*\)\s*;', '', html, flags=re.IGNORECASE)
            html = html.replace('initImageDropZones();', '')
            html = html.replace('showSaveIndicator();', '')

            for r2_url, local_rel in asset_map.items():
                html = html.replace(r2_url, local_rel)

            html = re.sub(fr'/preview/{re.escape(website_id)}/', '', html)
            html = re.sub(
                r'href=(["\'])home\.html(#[^"\']*)?(["\'])',
                lambda m: f'href={m.group(1)}index.html{m.group(2) or ""}{m.group(1)}',
                html, flags=re.IGNORECASE
            )

            with open(os.path.join(tmp_dir, out_name), 'w', encoding='utf-8') as f:
                f.write(html)

        # Write vercel.json
        safe_id = website_id.replace('-', '')
        vercel_json = { "name": f"website-ai-site-{safe_id[:12]}" }
        with open(os.path.join(tmp_dir, 'vercel.json'), 'w') as f:
            json.dump(vercel_json, f)

        # 6. Run Vercel Deploy via Subprocess
        print(f">>> [DEPLOY] Running Vercel CLI in {tmp_dir}", flush=True)
        cmd = 'npx.cmd' if os.name == 'nt' else 'npx'
        
        process = await asyncio.create_subprocess_exec(
            cmd, '--yes', 'vercel', 'deploy', '--prod', '--yes', '--token', vercel_token,
            cwd=tmp_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        out_str = stdout_bytes.decode('utf-8')
        err_str = stderr_bytes.decode('utf-8')
        
        if process.returncode != 0:
            print(f">>> [DEPLOY FAIL] {err_str}", flush=True)
            return jsonify({"error": "Vercel CLI failed", "details": err_str}), 500
            
        print(f">>> [DEPLOY OK] stdout: {out_str}", flush=True)
        
        # 7. EXTRACT URL (Robust multi-pattern regex)
        # We search for the standard .vercel.app link, including potential hyphens/underscores
        deploy_pattern = re.compile(r'https://[a-zA-Z0-9\-\._]+\.vercel\.app', re.IGNORECASE)
        urls = deploy_pattern.findall(out_str + err_str)
        
        if urls:
            # Vercel often outputs multiple URLs (preview, inspect, etc.)
            # We want the last actual deployment URL found in the stream.
            final_url = urls[-1]
            print(f">>> [DEPLOY DISCOVERY] Captured URL: {final_url}", flush=True)
            return jsonify({"success": True, "url": final_url})
        else:
            # Fallback: Log everything for debugging but attempt a generic failure message
            print(f">>> [DEPLOY PARSE FAIL] Full Output Context:\n{out_str}\n{err_str}", flush=True)
            return jsonify({
                "error": "Site successfully deployed to Vercel, but the automatic link-capture failed. Please check your Vercel Dashboard.", 
                "details": "Regex failed to find .vercel.app in CLI output"
            }), 500

    except Exception as e:
        print(f">>> [DEPLOY CRITICAL] {e}", flush=True)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        # 8. Cleanup temp folder (always executes)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print(f">>> [DEPLOY CLEANUP] Removed {tmp_dir}", flush=True)

@require_auth
async def list_websites():
    websites = []
    for folder in os.listdir(app.config['GENERATED_FOLDER']):
        websites.append({
            "id": folder,
            "preview_url": f"/preview/{folder}/home.html",
            "download_url": f"/download/{folder}"
        })
    return jsonify(websites)
 
@app.route('/save-and-build', methods=['POST'])
async def save_and_build():
    from flask import request
    data = request.json
    wid = data.get('website_id')
    pg  = os.path.basename(data.get('page_name', 'home.html'))
    fld = os.path.join(app.config['GENERATED_FOLDER'], wid)
    if os.path.exists(fld):
        with open(os.path.join(fld, pg), "w", encoding="utf-8") as f:
            f.write(data.get('html'))
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# CHAT-EDIT HELPERS
# ---------------------------------------------------------------------------

async def re_render_website(website_id: str, ctx: dict) -> str:
    """Re-renders home.html from updated context and saves to disk.  Returns preview_url."""
    data   = ctx["data"]
    layout = ctx["layout"]
    theme  = ctx["theme"]

    base_ctx = dict(
        site_name=ctx["site_name"], site_title=ctx["site_title"],
        tagline=ctx["tagline"], theme=theme, footer=ctx["footer"],
        layout=layout, image_map=ctx["image_map"],
        image_count=len(ctx["image_context"]),
        has_images=(len(ctx["image_context"]) > 0),
        logo=ctx["logo"]
    )
    home_html = render_template(
        "home.html", **base_ctx,
        home=data.get("home", {}),
        about=data.get("about", {}),
        services=data.get("services", []),
        portfolio=data.get("portfolio", []),
        testimonials=data.get("testimonials", []),
        faq=data.get("faq", []),
        pricing=data.get("pricing", []),
        stats=data.get("stats", []),
        contact=data.get("contact", {}),
        images=ctx["image_context"],
    )
    folder = ctx["website_folder"]
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "home.html"), "w", encoding="utf-8") as f:
        f.write(home_html)
    
    # Re-upload to R2
    await asyncio.to_thread(
        upload_media_to_r2,
        home_html.encode('utf-8'),
        "text/html",
        folder=f"websites/{website_id}",
        filename="home.html"
    )

    # Update the MongoDB with the new layout
    try:
        await update_website_layout(website_id, layout)
    except Exception as e:
        print(f">>> [DB UPDATE ERR] Could not update layout in MongoDB for {website_id}: {e}", flush=True)

    return f"/preview/{website_id}/home.html"


async def generate_section_content(ctx: dict, section_name: str) -> dict:
    """Calls AI to generate content for a single new section that wasn't in the original generation."""
    if not genai_client:
        print(">>> [ERROR] generate_section_content called but genai_client is None", flush=True)
        return {}
    # The new SDK uses the Client interface
    industry_label = INDUSTRY_TEMPLATES.get(ctx.get("industry", ""), {}).get("label", "general")
    section_prompt = f"""
Business: {ctx['prompt']}
Industry: {industry_label}

Generate ONLY the '{section_name}' section content for this website.
Return ONLY valid JSON matching one of these schemas:

- services: [{{'icon':'emoji','title':'str','desc':'str'}}] (exactly 4 items)
- faq:       [{{'q':'str','a':'str'}}] (4–6 items)
- pricing:   [{{'name':'str','price':'str','period':'str','features':['str'],'cta':'str','highlighted':bool}}] (2–3 tiers)
- testimonials: [{{'name':'str','role':'str','quote':'str','stars':5}}] (3 items)
- stats:     [{{'value':'str','label':'str'}}] (4 items)
- portfolio: [{{'tag':'str','title':'str','description':'str','client':'str','outcome':'str'}}] (3 items)
- about:     {{'headline':'str','body':'str','values':[{{'icon':'emoji','title':'str','desc':'str'}}]}}

Return ONLY the JSON array or object for '{section_name}'. NO other text.
"""
    response = await asyncio.to_thread(
        genai_client.models.generate_content, 
        model="gemini-3.1-flash-image-preview",
        contents=section_prompt,
        config={
            "system_instruction": system_prompt,
            "temperature": 0.7, 
            "max_output_tokens": 1500
        }
    )
    text = response.text.strip()
    text = re.sub(r'^```[a-z]*\n?', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n?```$', '', text, flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(text)
        return {section_name: parsed}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# /chat-edit  — AI-powered live editing of generated websites
# ---------------------------------------------------------------------------
@app.route('/chat-edit', methods=['POST'])
@require_auth
@require_credits(1)
async def chat_edit():
    if not ENABLE_CHAT_EDIT:
        return jsonify({"error": "Feature disabled in Phase 1", "status": "disabled"}), 403

    try:
        if not genai_client:
            return jsonify({"error": "AI client not initialized (Key missing?)", "status": "error"}), 500
        payload      = request.get_json()
        website_id   = payload.get('website_id', '')
        instruction  = payload.get('instruction', '').strip()
        current_html = payload.get('html', '').strip()
        page_name    = payload.get('page_name', 'home.html')

        if not website_id or not instruction or not current_html:
            return jsonify({'error': 'website_id, instruction, and current html are required'}), 400

        # Save user message for audit
        try:
            await insert_chat_message(website_id, 'user', instruction)
        except: pass

        # ─── AI modifies the FULL HTML using new GenAI SDK ───
        edit_prompt = f"""You are an expert web developer AI. 
Modify the following HTML based on the user's instruction.
Return the COMPLETE, UPDATED HTML. No explanations. No markdown formatting.

USER INSTRUCTION: "{instruction}"

CURRENT HTML:
{current_html}
"""

        response = await asyncio.to_thread(
            genai_client.models.generate_content,
            model="gemini-3.1-flash-image-preview",
            contents=edit_prompt,
            config={"temperature": 0.2}
        )
        
        updated_html = response.text.strip()
        # Clean AI markdown if any
        updated_html = re.sub(r'^```[a-z]*\n?', '', updated_html, flags=re.MULTILINE)
        updated_html = re.sub(r'\n?```$', '', updated_html, flags=re.MULTILINE).strip()

        # ─── 8. CREDIT DEDUCTION (Charge only on success) ───
        credit_result = await website_credits_debits({
            "type": "USAGE",
            "userId": g.user_id,
            "amount": -1,  # Deduct 1 credit per AI edit
            "resourceType": "chat_edit",
            "resourceId": website_id,
            "jobId": str(uuid.uuid4()),
            "description": f"AI Chat Edit for website {website_id}",
        })

        if not credit_result.get("success"):
            print(f">>> [CREDIT ERR] Failed to deduct credits for chat-edit: {credit_result.get('error')}", flush=True)
            return jsonify({
                "error": credit_result.get("error", "FAILED_TO_DEDUCT_CREDITS")
            }), 402

        # Save assistant message
        try:
            await insert_chat_message(website_id, 'assistant', "I've updated the website based on your instructions.")
        except: pass

        return jsonify({
            'success': True,
            'html': updated_html,
            'summary': "Website updated successfully"
        })

    except Exception as e:
        print(f">>> [CHAT-EDIT ERR] {e}", flush=True)
        return jsonify({'error': str(e)}), 500

    except json.JSONDecodeError:
        return jsonify({'error': 'AI could not understand that instruction. Try rephrasing it.'}), 400
    except Exception as e:
        print(f">>> [CHAT-EDIT ERR] {e}", flush=True)
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


if __name__ == "__main__":
    # Local development entry point
    env  = app.config.get("ENVIRONMENT", "development")
    port = int(os.environ.get("PORT", 5077))
    print(f"--- SERVER STARTING ON PORT {port} [{env}] ---", flush=True)
    app.run(host="127.0.0.1", port=port, debug=(env == "development"))
