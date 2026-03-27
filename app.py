import os
import uuid
import json
import re
import asyncio
import logging
import traceback
from flask import Flask, render_template, request, jsonify, send_file, g
from werkzeug.utils import secure_filename
import google.generativeai as genai
from PIL import Image
from config import Config
import uuid as pyuuid
from core.db import get_engine, get_session_factory
from model.website_schema import WebsiteInfo
from model.img_info_schema import ImageInfo
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import insert
from core.r2 import upload_media_to_r2, R2_PUBLIC_URL

# --- LOGGING SETUP ---
print(">>> [BOOT] App logging initializing...", flush=True)

app = Flask(__name__)
app.config.from_object(Config)

# Global flag for lazy DB initialization
_DB_READY = False

# --- AUTHENTICATION ---
@app.before_request
async def authenticate_request():
    global _DB_READY
    
    # Lazy Database initialization within the current request's event loop
    if not _DB_READY:
        from core.db import init_db
        try:
            await init_db()
            print(">>> [DB] Database verified on current loop", flush=True)
            _DB_READY = True
        except Exception as e:
            print(f">>> [DB ERR] Lazy init failed: {e}", flush=True)

    print(f"\n>>> [REQUEST] {request.method} {request.path}", flush=True)
    from core.auth import get_current_user_flask
    # Note: get_current_user_flask is internally sync, which is fine here
    user = get_current_user_flask()
    if user:
        g.user = user
        g.user_id = str(user.user_id)
        print(f">>> [AUTH] Success: {g.user_id}", flush=True)
    else:
        print(f">>> [AUTH] Unauthenticated request for {request.path}", flush=True)

def require_auth(f):
    from functools import wraps
    @wraps(f)
    async def decorated_function(*args, **kwargs):
        if not getattr(g, 'user_id', None):
            print(f">>> [DENIED] {request.path} - No user_id in g", flush=True)
            return jsonify({"error": "Authentication required", "status": "error"}), 401
        return await f(*args, **kwargs)
    return decorated_function

genai.configure(api_key=app.config['GEMINI_API_KEY'])

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

LAYOUT_BLUEPRINTS = {
    "restaurant":  ["hero", "about", "services", "testimonials", "portfolio", "contact"],
    "cafe":        ["hero", "about", "services", "portfolio", "contact"],
    "bakery":      ["hero", "services", "about", "portfolio", "contact"],
    "bar":         ["hero", "about", "services", "portfolio", "contact"],
    "hotel":       ["hero", "services", "about", "testimonials", "contact"],
    "spa":         ["hero", "about", "services", "testimonials", "contact"],
    "yoga":        ["hero", "about", "services", "testimonials", "contact"],
    "fitness":     ["hero", "stats", "services", "about", "testimonials", "contact"],
    "gym":         ["hero", "stats", "services", "about", "portfolio", "contact"],
    "law":         ["hero", "about", "services", "testimonials", "faq", "contact"],
    "finance":     ["hero", "about", "services", "stats", "testimonials", "contact"],
    "consulting":  ["hero", "services", "about", "testimonials", "faq", "contact"],
    "saas":        ["hero", "stats", "services", "about", "pricing", "faq", "contact"],
    "startup":     ["hero", "stats", "services", "about", "testimonials", "contact"],
    "software":    ["hero", "services", "about", "pricing", "faq", "contact"],
    "tech":        ["hero", "stats", "services", "about", "portfolio", "contact"],
    "agency":      ["hero", "portfolio", "services", "about", "testimonials", "contact"],
    "photography": ["hero", "portfolio", "about", "services", "testimonials", "contact"],
    "fashion":     ["hero", "portfolio", "about", "services", "contact"],
    "ecommerce":   ["hero", "services", "portfolio", "testimonials", "contact"],
    "medical":     ["hero", "services", "about", "faq", "contact"],
    "dental":      ["hero", "services", "about", "testimonials", "faq", "contact"],
    "real estate": ["hero", "services", "portfolio", "about", "testimonials", "contact"],
    "default":     ["hero", "about", "services", "portfolio", "contact"],
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
    for kw, layout in LAYOUT_BLUEPRINTS.items():
        if kw in p:
            return layout
    return LAYOUT_BLUEPRINTS["default"]

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

model = genai.GenerativeModel(
    "gemini-3.1-flash-image-preview",
    system_instruction=system_prompt
)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['GENERATED_FOLDER'], exist_ok=True)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']


async def generate_website_content(prompt, image_paths=None, image_count=0):
    # (Model initialization moved inside generate_website_content to avoid event loop issues)
    model = genai.GenerativeModel(
        "gemini-3.1-flash-image-preview",
        system_instruction=system_prompt
    )
    try:
        fallback = get_fallback_tokens(prompt)
        layout   = get_layout_blueprint(prompt)

        # Explicitly tell the AI how many portfolio entries to make to match the images
        expected_port_count = min(3, max(0, image_count - 2))
        image_summary = ""
        if image_count > 0:
            image_summary = f"\nIMAGE ALLOCATION (MUST MATCH):\n- Image 1: Hero\n- Image 2: About"
            if image_count >= 3:
                image_summary += f"\n- Images 3 to {min(5, image_count)}: Portfolio Case Studies (You MUST generate exactly {expected_port_count} items in the 'portfolio' list)."

        full_prompt = f"""Business: {prompt}
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

        print(f">>> [AI START] Model: {model.model_name} | Images: {image_count}", flush=True)
        
        # Using asyncio.to_thread with the synchronous generate_content method
        # for better stability and to avoid "Event loop is closed" errors with gRPC.
        response = await asyncio.to_thread(
            model.generate_content,
            content_parts,
            generation_config={"temperature": 0.85, "max_output_tokens": 4000}
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
    total = len(image_context)
    mapping = {"hero": None, "about": None, "portfolio": []}
    
    # 1st Image -> Hero
    if total >= 1: mapping["hero"] = image_context[0]
    
    # 2nd Image -> About
    if total >= 2: mapping["about"] = image_context[1]
    
    # 3rd, 4th, 5th -> Portfolio Grid
    if total >= 3: mapping["portfolio"] = image_context[2:5]
        
    return mapping


@app.route('/')
async def index():
    print(">>> [INDEX] Root page hit!", flush=True)
    return render_template('index.html')


@app.route('/generate', methods=['POST'])
@require_auth
async def generate_website():
    try:
        prompt = request.form.get('prompt', '')
        logo_file = request.files.get('logo')

        if any(kw in prompt.lower() for kw in ["upload image", "add images yourself", "generate image", "create image"]):
            return jsonify({"warning": "Please upload images manually. This AI generates branding and content, not the images themselves."}), 400

        files = request.files.getlist('images')

        if not prompt:
            return jsonify({'error': 'Please provide a description'}), 400

        if len(files) > MAX_IMAGES:
            return jsonify({"error": f"Max {MAX_IMAGES} images allowed."}), 400

        website_id = str(uuid.uuid4())
        website_folder = os.path.join(app.config['GENERATED_FOLDER'], website_id)
        os.makedirs(website_folder, exist_ok=True)

        import io
        db_image_records = []
        logo_web_path = None
        if logo_file and logo_file.filename and allowed_file(logo_file.filename):
            logo_filename = secure_filename(logo_file.filename)
            unique_logo_name = f"logo_{uuid.uuid4()}_{logo_filename}"
            logo_filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_logo_name)
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
                upload_media_to_r2,
                logo_bytes,
                logo_file.mimetype,
                folder=f"websites/{website_id}/assets"
            )
            
            db_image_records.append({
                "file_url": logo_web_path,
                "file_name": logo_filename,
                "file_format": l_format,
                "file_size_mb": len(logo_bytes) / (1024 * 1024),
                "width": l_width,
                "height": l_height,
                "image_type": "logo",
                "is_generated": False
            })

        image_context = []
        image_paths   = []

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
                    upload_media_to_r2,
                    file_bytes,
                    file.mimetype,
                    folder=f"websites/{website_id}/assets"
                )
                image_context.append(web_path)
                image_paths.append(filepath)
                
                img_type = "hero" if i == 0 else ("about" if i == 1 else "portfolio")
                db_image_records.append({
                    "file_url": web_path,
                    "file_name": filename,
                    "file_format": i_format,
                    "file_size_mb": len(file_bytes) / (1024 * 1024),
                    "width": i_width,
                    "height": i_height,
                    "image_type": img_type,
                    "is_generated": False
                })

        data = await generate_website_content(prompt, image_paths, len(image_paths))
        # ... and later pass logo=logo_web_path to base_ctx

        if not data:
            return jsonify({"error": "AI failed to generate content. Please try again."}), 500

        # website_id and folder are already created above now

        site_name  = data.get("site_info", {}).get("display_name", "My Business")
        site_title = data.get("site_info", {}).get("site_title", site_name)
        tagline    = data.get("site_info", {}).get("tagline", "")
        theme      = data.get("theme", {})
        footer     = data.get("footer", {})
        layout     = data.get("layout", ["hero", "about", "services", "portfolio", "contact"])
        image_map  = build_image_map(image_context, layout)

        base_ctx = dict(
            site_name=site_name, site_title=site_title,
            tagline=tagline, theme=theme, footer=footer,
            layout=layout, image_map=image_map,
            image_count=len(image_context),
            has_images=(len(image_context) > 0),
            logo=logo_web_path
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
            images=image_context,
        )
        with open(os.path.join(website_folder, "home.html"), "w", encoding="utf-8") as f:
            f.write(home_html)
        
        await asyncio.to_thread(
            upload_media_to_r2,
            home_html.encode('utf-8'),
            "text/html",
            folder=f"websites/{website_id}",
            filename="home.html"
        )

        page_templates = {
            "about.html":     ("about.html",     dict(**base_ctx, about=data.get("about",{}), services=data.get("services",[]), images=image_context)),
            "services.html":  ("services.html",  dict(**base_ctx, services=data.get("services",[]), images=image_context)),
            "portfolio.html": ("portfolio.html", dict(**base_ctx, portfolio=data.get("portfolio",[]), images=image_context)),
            "contact.html":   ("contact.html",   dict(**base_ctx, contact=data.get("contact",{}), images=image_context)),
        }
        for out_name, (tmpl, ctx) in page_templates.items():
            html = render_template(tmpl, **ctx)
            with open(os.path.join(website_folder, out_name), "w", encoding="utf-8") as f:
                f.write(html)
            
            await asyncio.to_thread(
                upload_media_to_r2,
                html.encode('utf-8'),
                "text/html",
                folder=f"websites/{website_id}",
                filename=out_name
            )

        # --- DATABASE PERSISTENCE ---
        try:
            # Using lazy engine from get_engine() for absolute stability
            eng = get_engine()
            async with eng.begin() as conn:
                from sqlalchemy import insert
                await conn.execute(
                    insert(WebsiteInfo).values(
                        website_id=pyuuid.UUID(website_id),
                        user_id=pyuuid.UUID(g.user_id),
                        prompt=prompt,
                        status="completed",
                        progress="100",
                        final_url=f"{R2_PUBLIC_URL}/websites/{website_id}/home.html"
                    )
                )
                
                for record in db_image_records:
                    await conn.execute(
                        insert(ImageInfo).values(
                            website_id=pyuuid.UUID(website_id),
                            file_url=record["file_url"],
                            file_name=record["file_name"],
                            file_format=record["file_format"],
                            file_size_mb=record["file_size_mb"],
                            width=record["width"],
                            height=record["height"],
                            image_type=record["image_type"],
                            is_generated=record["is_generated"]
                        )
                    )
                    
            print(f">>> [DB] Saved website and {len(db_image_records)} image records: {website_id}", flush=True)
        except Exception as db_err:
            print(f">>> [DB ERR] Failed to persist info: {db_err}", flush=True)
            # We don't return 500 here because the website WAS generated successfully on disk
            # but we definitely want to see the error in logs.

        return jsonify({
            "success": True,
            "website_id": website_id,
            "preview_url": f"/preview/{website_id}/home.html",
            "download_url": f"/download/{website_id}",
            "layout": layout,
            "image_count": len(image_context)
        })

    except Exception as e:
        print(f">>> [ROUTE CRITICAL] /generate - {str(e)}", flush=True)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/preview/<website_id>/<page>')
async def preview_website(website_id, page):
    page = os.path.basename(page)
    path = os.path.join(app.config['GENERATED_FOLDER'], website_id, page)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return "Website not found", 404


@app.route('/download/<website_id>')
async def download_website(website_id):
    folder = os.path.join(app.config['GENERATED_FOLDER'], website_id)
    if os.path.exists(folder):
        return send_file(
            os.path.join(folder, "home.html"),
            as_attachment=True,
            download_name=f"website_{website_id}.html",
            mimetype="text/html"
        )
    return "Website not found", 404


@app.route('/list-websites')
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
@require_auth
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


if __name__ == "__main__":
    print("--- SERVER STARTING ON PORT 5077 ---", flush=True)
    app.run(debug=True, port=5077, use_reloader=False)