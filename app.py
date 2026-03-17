import os
import uuid
import json
import re
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename
import google.generativeai as genai
from PIL import Image
from config import Config

app = Flask(__name__)
app.config.from_object(Config)

genai.configure(api_key=app.config['GEMINI_API_KEY'])

MAX_IMAGES = app.config["MAX_IMAGES"]

# ---------------------------------------------------------------------------
# NICHE → DESIGN TOKENS
# ---------------------------------------------------------------------------
NICHE_DESIGN = {
    "restaurant":   {"font_heading": "Cormorant Garamond", "font_body": "Lato",            "primary": "#c0392b", "primary_dark": "#2c1810", "bg": "#fdf6ec", "bg_alt": "#f5ede0", "text_main": "#1a0f0a", "text_muted": "#7a5c4e", "accent": "#e67e22", "mood": "warm intimate dining"},
    "cafe":         {"font_heading": "Playfair Display",   "font_body": "Source Sans Pro",  "primary": "#3d1f0d", "primary_dark": "#1a0a04", "bg": "#fef9f0", "bg_alt": "#f5ede0", "text_main": "#1a0f0a", "text_muted": "#7a5c4e", "accent": "#8a9a5b", "mood": "artisanal cozy"},
    "bakery":       {"font_heading": "Fraunces",           "font_body": "Nunito",           "primary": "#c9806b", "primary_dark": "#3d1f0d", "bg": "#fffbf5", "bg_alt": "#f9f0e6", "text_main": "#2d1810", "text_muted": "#8a6858", "accent": "#e8a598", "mood": "artisan patisserie"},
    "bar":          {"font_heading": "Bebas Neue",         "font_body": "Raleway",          "primary": "#d4a017", "primary_dark": "#0d1b2a", "bg": "#0d1b2a", "bg_alt": "#12243a", "text_main": "#f5f0e8", "text_muted": "#94a3b8", "accent": "#d4a017", "mood": "moody sophisticated"},
    "hotel":        {"font_heading": "Cormorant Garamond", "font_body": "Jost",             "primary": "#8b7355", "primary_dark": "#1a1a1a", "bg": "#faf5eb", "bg_alt": "#f0e8d8", "text_main": "#1a1a1a", "text_muted": "#6b5a45", "accent": "#c9a84c", "mood": "luxury hospitality"},
    "spa":          {"font_heading": "Cormorant Garamond", "font_body": "Josefin Sans",     "primary": "#7c8c7a", "primary_dark": "#3d4a3b", "bg": "#fdfaf7", "bg_alt": "#f0ebe4", "text_main": "#2a2a2a", "text_muted": "#7a7a6a", "accent": "#a89a7c", "mood": "serene minimal"},
    "yoga":         {"font_heading": "Cormorant Garamond", "font_body": "Karla",            "primary": "#7c9a6e", "primary_dark": "#2d4a28", "bg": "#f5efe0", "bg_alt": "#ece4d0", "text_main": "#1a2a18", "text_muted": "#6a7a5e", "accent": "#c97b4b", "mood": "organic calm"},
    "fitness":      {"font_heading": "Barlow Condensed",   "font_body": "Barlow",           "primary": "#ef233c", "primary_dark": "#0a0a0a", "bg": "#f8f8f8", "bg_alt": "#efefef", "text_main": "#0a0a0a", "text_muted": "#555555", "accent": "#ef233c", "mood": "athletic power"},
    "gym":          {"font_heading": "Bebas Neue",         "font_body": "Oswald",           "primary": "#ff4500", "primary_dark": "#0a0a0a", "bg": "#f5f5f5", "bg_alt": "#ebebeb", "text_main": "#111111", "text_muted": "#555555", "accent": "#ff4500", "mood": "raw intensity"},
    "wellness":     {"font_heading": "Fraunces",           "font_body": "Karla",            "primary": "#2d6a4f", "primary_dark": "#1a3d2e", "bg": "#faf0e6", "bg_alt": "#f0e4d4", "text_main": "#1a2a1a", "text_muted": "#5a7a5a", "accent": "#d4a853", "mood": "natural holistic"},
    "medical":      {"font_heading": "Nunito",             "font_body": "Source Sans Pro",  "primary": "#0d9488", "primary_dark": "#1e3a5f", "bg": "#f8fafc", "bg_alt": "#eef2f7", "text_main": "#0f172a", "text_muted": "#475569", "accent": "#0ea5e9", "mood": "trustworthy clinical"},
    "dental":       {"font_heading": "Nunito",             "font_body": "Lato",             "primary": "#0ea5e9", "primary_dark": "#0369a1", "bg": "#f0f9ff", "bg_alt": "#e0f2fe", "text_main": "#0c1a2e", "text_muted": "#475569", "accent": "#06b6d4", "mood": "fresh friendly"},
    "law":          {"font_heading": "Cormorant Garamond", "font_body": "Lato",             "primary": "#c9a84c", "primary_dark": "#0c1b33", "bg": "#faf8f2", "bg_alt": "#f0ece0", "text_main": "#0c1b33", "text_muted": "#5a5a4a", "accent": "#c9a84c", "mood": "authoritative formal"},
    "finance":      {"font_heading": "Libre Baskerville",  "font_body": "Source Sans Pro",  "primary": "#059669", "primary_dark": "#0f1f3d", "bg": "#f8fafc", "bg_alt": "#eef2f7", "text_main": "#0f172a", "text_muted": "#475569", "accent": "#10b981", "mood": "stable trustworthy"},
    "consulting":   {"font_heading": "Montserrat",         "font_body": "Open Sans",        "primary": "#d97706", "primary_dark": "#1c1c1c", "bg": "#fafafa", "bg_alt": "#f0f0f0", "text_main": "#1a1a1a", "text_muted": "#555555", "accent": "#f59e0b", "mood": "professional results"},
    "real estate":  {"font_heading": "Cormorant Garamond", "font_body": "Jost",             "primary": "#64748b", "primary_dark": "#1e293b", "bg": "#faf8f5", "bg_alt": "#f0ece6", "text_main": "#1e293b", "text_muted": "#64748b", "accent": "#94a3b8", "mood": "premium aspirational"},
    "photography":  {"font_heading": "Cormorant Garamond", "font_body": "Lato",             "primary": "#c9a84c", "primary_dark": "#111111", "bg": "#f8f8f5", "bg_alt": "#efefeb", "text_main": "#111111", "text_muted": "#555555", "accent": "#c9a84c", "mood": "editorial cinematic"},
    "fashion":      {"font_heading": "Bodoni Moda",        "font_body": "Raleway",          "primary": "#111111", "primary_dark": "#000000", "bg": "#fafaf8", "bg_alt": "#f0f0ee", "text_main": "#0a0a0a", "text_muted": "#555555", "accent": "#8a7a6a", "mood": "high fashion editorial"},
    "interior":     {"font_heading": "Cormorant Garamond", "font_body": "Jost",             "primary": "#8a6a4a", "primary_dark": "#2a1a0a", "bg": "#f8f4ee", "bg_alt": "#eee8e0", "text_main": "#1a1210", "text_muted": "#7a6a5a", "accent": "#c9a87c", "mood": "luxury residential"},
    "architecture": {"font_heading": "Montserrat",         "font_body": "Libre Franklin",   "primary": "#555555", "primary_dark": "#111111", "bg": "#f8f8f8", "bg_alt": "#eeeeee", "text_main": "#111111", "text_muted": "#666666", "accent": "#333333", "mood": "brutalist structural"},
    "design":       {"font_heading": "Syne",               "font_body": "DM Sans",          "primary": "#111111", "primary_dark": "#000000", "bg": "#f7f6f3", "bg_alt": "#eeede8", "text_main": "#0a0a0a", "text_muted": "#555555", "accent": "#ffe600", "mood": "meta experimental"},
    "saas":         {"font_heading": "Syne",               "font_body": "DM Sans",          "primary": "#06b6d4", "primary_dark": "#0f172a", "bg": "#f8fafc", "bg_alt": "#eef2f7", "text_main": "#0f172a", "text_muted": "#475569", "accent": "#0ea5e9", "mood": "technical credibility"},
    "software":     {"font_heading": "Space Grotesk",      "font_body": "IBM Plex Sans",    "primary": "#00c896", "primary_dark": "#050a0f", "bg": "#f0f4f8", "bg_alt": "#e4eaf0", "text_main": "#050a0f", "text_muted": "#4a5568", "accent": "#00ff88", "mood": "engineering precision"},
    "startup":      {"font_heading": "Syne",               "font_body": "Plus Jakarta Sans","primary": "#ff6b35", "primary_dark": "#111827", "bg": "#f9fafb", "bg_alt": "#f0f1f3", "text_main": "#111827", "text_muted": "#4b5563", "accent": "#ff8c5a", "mood": "disruptive ambitious"},
    "ai":           {"font_heading": "Syne",               "font_body": "IBM Plex Sans",    "primary": "#3b82f6", "primary_dark": "#050810", "bg": "#f0f4ff", "bg_alt": "#e4ebff", "text_main": "#050810", "text_muted": "#4a5568", "accent": "#60a5fa", "mood": "futuristic intelligent"},
    "tech":         {"font_heading": "Exo 2",              "font_body": "Manrope",          "primary": "#6366f1", "primary_dark": "#0f172a", "bg": "#f8fafc", "bg_alt": "#eef2f7", "text_main": "#0f172a", "text_muted": "#475569", "accent": "#818cf8", "mood": "modern geometric"},
    "app":          {"font_heading": "Syne",               "font_body": "DM Sans",          "primary": "#7c3aed", "primary_dark": "#1e0a3c", "bg": "#faf8ff", "bg_alt": "#f0ebff", "text_main": "#1e0a3c", "text_muted": "#6b5b9a", "accent": "#a78bfa", "mood": "product-led bold"},
    "portfolio":    {"font_heading": "Cormorant Garamond", "font_body": "DM Sans",          "primary": "#111111", "primary_dark": "#000000", "bg": "#fafafa", "bg_alt": "#f0f0f0", "text_main": "#111111", "text_muted": "#555555", "accent": "#333333", "mood": "minimal work-forward"},
    "agency":       {"font_heading": "Syne",               "font_body": "DM Sans",          "primary": "#111111", "primary_dark": "#000000", "bg": "#f8f8f8", "bg_alt": "#eeeeee", "text_main": "#0a0a0a", "text_muted": "#555555", "accent": "#a3e635", "mood": "bold creative"},
    "school":       {"font_heading": "Nunito",             "font_body": "Lato",             "primary": "#1e40af", "primary_dark": "#1e3a8a", "bg": "#f0f4ff", "bg_alt": "#e4ebff", "text_main": "#0f172a", "text_muted": "#475569", "accent": "#fbbf24", "mood": "welcoming community"},
    "coaching":     {"font_heading": "Fraunces",           "font_body": "Karla",            "primary": "#92400e", "primary_dark": "#451a03", "bg": "#fef9f0", "bg_alt": "#f5ede0", "text_main": "#1c0a00", "text_muted": "#7a5030", "accent": "#d97706", "mood": "personal transformative"},
    "nonprofit":    {"font_heading": "Fraunces",           "font_body": "Source Sans Pro",  "primary": "#166534", "primary_dark": "#052e16", "bg": "#f0fdf4", "bg_alt": "#dcfce7", "text_main": "#052e16", "text_muted": "#4a7a5a", "accent": "#f59e0b", "mood": "mission driven"},
    "wedding":      {"font_heading": "Cormorant Garamond", "font_body": "Lato",             "primary": "#c9a87c", "primary_dark": "#3d1f0d", "bg": "#fffbf5", "bg_alt": "#f9f0e6", "text_main": "#2d1810", "text_muted": "#8a6858", "accent": "#e8c5a0", "mood": "romantic elegant"},
    "event":        {"font_heading": "Bebas Neue",         "font_body": "Raleway",          "primary": "#facc15", "primary_dark": "#0a0a0a", "bg": "#0a0a0a", "bg_alt": "#141414", "text_main": "#fafafa", "text_muted": "#aaaaaa", "accent": "#fde047", "mood": "bold excitement"},
    "music":        {"font_heading": "Syne",               "font_body": "DM Sans",          "primary": "#7c3aed", "primary_dark": "#0a0a0a", "bg": "#0a0a0a", "bg_alt": "#141414", "text_main": "#fafafa", "text_muted": "#aaaaaa", "accent": "#a78bfa", "mood": "dark atmospheric"},
    "jewelry":      {"font_heading": "Cormorant Garamond", "font_body": "Raleway",          "primary": "#c9a84c", "primary_dark": "#111111", "bg": "#fafaf8", "bg_alt": "#f2f0eb", "text_main": "#111111", "text_muted": "#666655", "accent": "#e8c87a", "mood": "luxury desire"},
    "clothing":     {"font_heading": "Bodoni Moda",        "font_body": "Raleway",          "primary": "#111111", "primary_dark": "#000000", "bg": "#fafaf8", "bg_alt": "#f2f0ee", "text_main": "#0a0a0a", "text_muted": "#555555", "accent": "#888888", "mood": "fashion editorial"},
    "ecommerce":    {"font_heading": "Syne",               "font_body": "DM Sans",          "primary": "#111827", "primary_dark": "#030712", "bg": "#ffffff", "bg_alt": "#f9fafb", "text_main": "#111827", "text_muted": "#6b7280", "accent": "#3b82f6", "mood": "product conversion"},
    "shop":         {"font_heading": "Nunito",             "font_body": "Lato",             "primary": "#2563eb", "primary_dark": "#1d4ed8", "bg": "#ffffff", "bg_alt": "#f8fafc", "text_main": "#0f172a", "text_muted": "#475569", "accent": "#3b82f6", "mood": "clean retail"},
    "food":         {"font_heading": "Fraunces",           "font_body": "DM Sans",          "primary": "#e63946", "primary_dark": "#9b1b24", "bg": "#fff8f0", "bg_alt": "#ffeedd", "text_main": "#1a0a04", "text_muted": "#7a4a3a", "accent": "#f4a261", "mood": "vibrant appetite"},
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
        "font_heading": "Syne", "font_body": "DM Sans",
        "primary": "#111827", "primary_dark": "#030712",
        "bg": "#ffffff", "bg_alt": "#f9fafb",
        "text_main": "#111827", "text_muted": "#6b7280",
        "accent": "#3b82f6", "mood": "professional modern"
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
    if fixed.get("hero_style") not in ["split-left", "fullbleed", "bold-center"]:
        fixed["hero_style"] = "split-left"
    if fixed["hero_style"] == "fullbleed" and not has_image:
        # Without a real photo, fullbleed just looks like a color wash
        # Dark bg → bold-center, light bg → split-left
        fixed["hero_style"] = "bold-center" if bg_lum < 0.15 else "split-left"
    if bg_lum < 0.15 and not has_image:
        # Very dark bg (gym black, bar navy) → bold-center looks premium
        fixed["hero_style"] = "bold-center"

    # 8. card_style
    if fixed.get("card_style") not in ["flat", "outlined", "elevated"]:
        fixed["card_style"] = "elevated"

    # 9. divider_style
    if fixed.get("divider_style") not in ["diagonal", "wave", "none"]:
        fixed["divider_style"] = "diagonal"

    # 10. Font guards
    bad_heading_fonts = ["Inter", "Roboto", "Arial", "system-ui", "sans-serif", ""]
    if not fixed.get("font_heading") or fixed["font_heading"] in bad_heading_fonts:
        fixed["font_heading"] = fallback.get("font_heading", "Syne")
    if not fixed.get("font_body") or fixed["font_body"] in ["Arial", "system-ui", "sans-serif", ""]:
        fixed["font_body"] = fallback.get("font_body", "DM Sans")

    return fixed

# ---------------------------------------------------------------------------
# SYSTEM PROMPT
# ---------------------------------------------------------------------------
system_prompt = """
You are a professional website copywriter and brand strategist.
Write CONTENT for a website — real headlines, real copy, real descriptions.

You will receive DEVELOPER DESIGN TOKENS. These set the visual style.
NEVER put design token values (color names, font names, palette words) into any copy field.

CRITICAL COPY RULES:
- BAD title: "Delicate Patisserie in Blush Pinks and Warm Whites"  ← mentions colors
- GOOD title: "Every Bite, A Moment to Savour"  ← real marketing copy
- BAD subtitle: "Crafted in warm terracotta tones"  ← mentions palette
- GOOD subtitle: "Handmade pastries baked fresh every morning using local flour."

Return ONLY valid JSON. No markdown fences. No text outside JSON.

{
  "theme": {
    "font_heading": "EXACT_GOOGLE_FONT_FROM_TOKENS",
    "font_body": "EXACT_GOOGLE_FONT_FROM_TOKENS",
    "primary": "#hex",
    "primary_dark": "#hex",
    "primary_light": "rgba(r,g,b,0.12)",
    "accent": "#hex",
    "bg": "#hex",
    "bg_alt": "#hex slightly different from bg",
    "text_main": "#hex dark enough for 4.5:1 contrast on bg",
    "text_muted": "#hex",
    "nav_bg": "rgba(r,g,b,0.92)",
    "hero_style": "split-left",
    "card_style": "elevated",
    "divider_style": "diagonal"
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
    {"title": "Real service name", "description": "2 sentences.", "icon_type": "zap"}
  ],
  "portfolio": [
    {"title": "Real project name", "description": "One result sentence.", "tag": "Category"},
    {"title": "Real project name", "description": "One result sentence.", "tag": "Category"},
    {"title": "Real project name", "description": "One result sentence.", "tag": "Category"}
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

ABSOLUTE RULES:
1. Return ONLY JSON. No markdown. No text outside.
2. hero_style: ONLY "split-left", "fullbleed", or "bold-center"
3. card_style: ONLY "flat", "outlined", or "elevated"
4. divider_style: ONLY "diagonal", "wave", or "none"
5. Fonts MUST be real Google Fonts. NEVER Inter, Roboto, Arial as font_heading.
6. primary_light MUST be rgba of primary at 0.10-0.15 opacity.
7. bg and bg_alt MUST be different shades.
8. text_main MUST have 4.5:1 contrast ratio against bg.
9. ALL copy specific to THIS business — zero generic filler.
10. ZERO color/font/design words in any copy field.
11. layout only includes sections making sense for this business.
12. pricing ONLY for SaaS/subscription businesses.
13. testimonials sound like real people with specific real details.
14. stats realistic for this exact business type.
"""

model = genai.GenerativeModel(
    "gemini-3.1-flash-image-preview",
    system_instruction=system_prompt
)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['GENERATED_FOLDER'], exist_ok=True)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']


def generate_website_content(prompt, image_paths=None, image_count=0):
    try:
        fallback = get_fallback_tokens(prompt)
        layout   = get_layout_blueprint(prompt)

        full_prompt = f"""Business: {prompt}

DEVELOPER DESIGN TOKENS (for theme JSON only — do NOT use these words in copy):
- font_heading: {fallback['font_heading']}
- font_body: {fallback['font_body']}
- primary color: {fallback['primary']}
- primary_dark: {fallback['primary_dark']}
- bg: {fallback['bg']}
- bg_alt: {fallback['bg_alt']}
- text_main: {fallback['text_main']}
- text_muted: {fallback['text_muted']}
- accent: {fallback['accent']}
- mood: {fallback['mood']}

SUGGESTED LAYOUT: {layout}
IMAGES UPLOADED: {image_count}
{"Analyze uploaded images for color and mood." if image_count > 0 else "No images — generate theme from business type only."}

Write copy like a real human copywriter for THIS specific business. Zero generic phrases."""

        content_parts = [full_prompt]
        if image_paths:
            for path in image_paths:
                try:
                    img = Image.open(path)
                    content_parts.append(img)
                except Exception as e:
                    print(f"Skipping image {path}: {e}")

        response = model.generate_content(
            content_parts,
            generation_config={"temperature": 0.75, "max_output_tokens": 4000}
        )

        text = response.text.strip()
        text = re.sub(r'^```[a-z]*\n?', '', text, flags=re.MULTILINE)
        text = re.sub(r'\n?```$', '', text, flags=re.MULTILINE)
        text = text.strip()

        data = json.loads(text)

        # Auto-fix theme values
        data["theme"] = validate_and_fix_theme(
            data.get("theme", {}), fallback, has_image=(image_count > 0)
        )

        return data

    except Exception as e:
        print("AI Error:", e)
        return None


def build_image_map(image_context: list, layout: list) -> dict:
    total   = len(image_context)
    mapping = {}
    if total == 0:
        return mapping
    mapping["hero"] = image_context[0]
    if total == 1:
        mapping["about"] = image_context[0]
        return mapping
    if total >= 2:
        mapping["about"] = image_context[1]
    if total > 2:
        mapping["portfolio"] = image_context[2:]
    return mapping


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/generate', methods=['POST'])
def generate_website():
    try:
        prompt = request.form.get('prompt', '')

        if "upload image" in prompt.lower() or "add images yourself" in prompt.lower():
            return jsonify({"warning": "Please upload images manually."}), 400

        files = request.files.getlist('images')

        if not prompt:
            return jsonify({'error': 'Please provide a description'}), 400

        if len(files) > MAX_IMAGES:
            return jsonify({"error": f"Max {MAX_IMAGES} images allowed."}), 400

        image_context = []
        image_paths   = []

        for file in files:
            if file and file.filename and allowed_file(file.filename):
                filename        = secure_filename(file.filename)
                unique_filename = f"{uuid.uuid4()}_{filename}"
                filepath        = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                file.save(filepath)
                web_path = "/" + filepath.replace("\\", "/")
                image_context.append(web_path)
                image_paths.append(filepath)

        data = generate_website_content(prompt, image_paths, len(image_paths))

        if not data:
            return jsonify({"error": "AI failed to generate content. Please try again."}), 500

        website_id     = str(uuid.uuid4())
        website_folder = os.path.join(app.config['GENERATED_FOLDER'], website_id)
        os.makedirs(website_folder, exist_ok=True)

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
            image_count=len(image_context)
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

        page_templates = {
            "about.html":     ("about.html",     dict(**base_ctx, about=data.get("about",{}), images=image_context)),
            "services.html":  ("services.html",  dict(**base_ctx, services=data.get("services",[]), images=image_context)),
            "portfolio.html": ("portfolio.html", dict(**base_ctx, portfolio=data.get("portfolio",[]), images=image_context)),
            "contact.html":   ("contact.html",   dict(**base_ctx, contact=data.get("contact",{}), images=image_context)),
        }
        for out_name, (tmpl, ctx) in page_templates.items():
            html = render_template(tmpl, **ctx)
            with open(os.path.join(website_folder, out_name), "w", encoding="utf-8") as f:
                f.write(html)

        return jsonify({
            "success": True,
            "website_id": website_id,
            "preview_url": f"/preview/{website_id}/home.html",
            "download_url": f"/download/{website_id}",
            "layout": layout,
            "image_count": len(image_context)
        })

    except Exception as e:
        print("Generate error:", e)
        return jsonify({"error": str(e)}), 500


@app.route('/preview/<website_id>/<page>')
def preview_website(website_id, page):
    page = os.path.basename(page)
    path = os.path.join(app.config['GENERATED_FOLDER'], website_id, page)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return "Website not found", 404


@app.route('/download/<website_id>')
def download_website(website_id):
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
def list_websites():
    websites = []
    for folder in os.listdir(app.config['GENERATED_FOLDER']):
        websites.append({
            "id": folder,
            "preview_url": f"/preview/{folder}/home.html",
            "download_url": f"/download/{folder}"
        })
    return jsonify(websites)


if __name__ == "__main__":
    app.run(debug=True, port=5000)