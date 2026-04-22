import random

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

PALETTE_MAP = {
    "luxury":    {"primary": "#c9a96e", "primary_dark": "#a07840", "bg": "#0f0f0f", "bg_alt": "#1a1a1a", "text_main": "#f5f0e8", "text_muted": "#b8a98a", "accent": "#c9a96e"},
    "brutalist": {"primary": "#000000", "primary_dark": "#000000", "bg": "#ffffff", "bg_alt": "#f0f0f0", "text_main": "#000000", "text_muted": "#444444", "accent": "#f5c842"},
    "soft":      {"primary": "#c2848a", "primary_dark": "#a06068", "bg": "#fdf6f0", "bg_alt": "#f9ede4", "text_main": "#4a2f35", "text_muted": "#9a6f75", "accent": "#f0b8bc"},
    "dark":      {"primary": "#7c6af7", "primary_dark": "#5b48e0", "bg": "#0d0d14", "bg_alt": "#14141f", "text_main": "#e8e6ff", "text_muted": "#8b87c0", "accent": "#7c6af7"},
    "earthy":    {"primary": "#9b6a44", "primary_dark": "#7a4f2e", "bg": "#f5f0e8", "bg_alt": "#ede5d8", "text_main": "#2d1f10", "text_muted": "#7a6045", "accent": "#c4894e"},
    "neon":      {"primary": "#00ffcc", "primary_dark": "#00c9a0", "bg": "#050510", "bg_alt": "#0a0a1a", "text_main": "#e0fffa", "text_muted": "#00ffcc", "accent": "#ff2d78"},
    "midnight":  {"primary": "#4338ca", "primary_dark": "#312e81", "bg": "#ffffff", "bg_alt": "#f8fafc", "text_main": "#1e1b4b", "text_muted": "#4338ca", "accent": "#f59e0b"},
    "slate":     {"primary": "#334155", "primary_dark": "#1e293b", "bg": "#fdfcfa", "bg_alt": "#f4f2ef", "text_main": "#1e293b", "text_muted": "#475569", "accent": "#f59e0b"},
    "crimson":   {"primary": "#991b1b", "primary_dark": "#7f1d1d", "bg": "#fffcf9", "bg_alt": "#fef2f2", "text_main": "#7f1d1d", "text_muted": "#dc2626", "accent": "#f59e0b"},
    "forest":    {"primary": "#065f46", "primary_dark": "#064e3b", "bg": "#fcfaf8", "bg_alt": "#f4f0ec", "text_main": "#064e3b", "text_muted": "#059669", "accent": "#f59e0b"},
    "ocean":     {"primary": "#1e40af", "primary_dark": "#1e3a8a", "bg": "#ffffff", "bg_alt": "#f8fafc", "text_main": "#1e293b", "text_muted": "#3b82f6", "accent": "#f59e0b"},
    "gold":      {"primary": "#92400e", "primary_dark": "#78350f", "bg": "#fffcf9", "bg_alt": "#f7f3f0", "text_main": "#451a03", "text_muted": "#b45309", "accent": "#10b981"},
    "rose":      {"primary": "#9d174d", "primary_dark": "#831843", "bg": "#ffffff", "bg_alt": "#fdf2f8", "text_main": "#500724", "text_muted": "#be185d", "accent": "#f59e0b"},
    "onyx":      {"primary": "#1c1917", "primary_dark": "#000000", "bg": "#ffffff", "bg_alt": "#fafaf9", "text_main": "#1c1917", "text_muted": "#78716c", "accent": "#f59e0b"},
}

INDUSTRY_TEMPLATES = {
    "restaurant": {
        "label": "Restaurant / Food",
        "inject": "INDUSTRY DESIGN BRIEF: Restaurant & Dining\nVISUAL DIRECTION: Warm, sensory, and inviting. Make visitors feel hungry and welcome.\nTONE: Artisan, neighborhood, beloved. Use words like: crafted, seasonal, sourced, slow-fermented, wood-fired, local.\nSECTIONS TO PRIORITIZE: Dramatic full-bleed hero with atmosphere shot, chef story in About, menu categories in Services, diner reviews in Testimonials, opening hours + reservation in Contact.\nKEY CTAs: 'Reserve a Table', 'View Menu', 'Book Private Dining', 'Order Online'\nFONTS DIRECTION: Serif headings for warmth (Playfair Display or Fraunces). Clean body font.\nPALETTE DIRECTION: Warm ivory backgrounds, deep burgundy or forest green primary, gold accents.\nBANNED WORDS: fast, quick, efficient, solution, platform, scalable.",
        "default_sections": ["hero", "about", "services", "stats", "testimonials", "contact"],
        "palette_hint": "earthy",
    },
    "real estate": {
        "label": "Real Estate / Property",
        "inject": "INDUSTRY DESIGN BRIEF: Real Estate & Property\nVISUAL DIRECTION: Professional, clean, and spacious. Focus on high-quality structural photography.\nTONE: Authoritative, trusted, high-value. Use words like: architectural, curated, residential, lifestyle, strategic, legacy.\nSECTIONS TO PRIORITIZE: High-impact structural hero, stats in About, listing categories in Services, property portfolio in Portfolio, client trust in Testimonials, location + team in Contact.\nKEY CTAs: 'View Listings', 'Book Valuation', 'Contact an Agent', 'Explore Properties'\nFONTS DIRECTION: Modern sans-serif (Outfit or Plus Jakarta Sans).\nPALETTE DIRECTION: Slate greys, clean whites, deep navy primary, subtle gold or teal accents.\nBANNED WORDS: cheap, bargain, quick-sale, easy-money.",
        "default_sections": ["hero", "stats", "services", "portfolio", "testimonials", "contact"],
        "palette_hint": "slate",
    },
    "tech": {
        "label": "Technology / SaaS",
        "inject": "INDUSTRY DESIGN BRIEF: Technology & Software\nVISUAL DIRECTION: Clean, modern, and data-driven. Focus on interface shots or abstract patterns.\nTONE: Innovative, scalable, visionary. Use words like: infrastructure, deployment, ecosystem, intelligence, protocol, core.\nSECTIONS TO PRIORITIZE: Visual software hero, mission in About, core modules in Services, case studies in Portfolio, user trust in Testimonials, API / partnership in Contact.\nKEY CTAs: 'Get Started', 'View Demo', 'Read Documentation', 'Contact Sales'\nFONTS DIRECTION: Geometric sans-serif (Inter or Roboto).\nPALETTE DIRECTION: Midnight blues, neon cyan accents, dark mode backgrounds.\nBANNED WORDS: old-school, traditional, legacy, manual.",
        "default_sections": ["hero", "services", "stats", "about", "portfolio", "contact"],
        "palette_hint": "midnight",
    }
}

system_prompt_text = """You are a high-end Design Director & Full-Stack Architect. Your goal is to generate 100% complete, realistic, and premium website data.
The response MUST be a single, valid JSON object following this schema exactly:
{
  "site_info": { "display_name": "...", "site_title": "...", "tagline": "..." },
  "home": { "hero_title": "...", "hero_subtitle": "...", "cta_text": "..." },
  "about": { "title": "...", "story": "...", "mission": "..." },
  "services": [ { "title": "...", "desc": "...", "icon": "..." } ],
  "portfolio": [ { "title": "...", "client": "...", "description": "..." } ],
  "testimonials": [ { "content": "...", "author": "...", "role": "..." } ],
  "faq": [ { "q": "...", "a": "..." } ],
  "stats": [ { "label": "...", "value": "..." } ],
  "pricing": [ { "name": "...", "price": "...", "period": "/mo", "description": "...", "features": ["...", "..."], "highlighted": false } ],
  "contact": { "title": "...", "description": "...", "email": "...", "phone": "...", "address": "..." },
  "footer": { "copyright": "...", "address": "..." },
  "theme": { "primary": "#...", "bg": "#...", "accent": "#...", "font_heading": "...", "font_body": "...", "hero_style": "...", "card_style": "...", "divider_style": "..." },
  "layout": ["hero", "about", ...]
}
IMPORTANT: Always include "contact" and "pricing" keys in the response even if they are not in the layout — the system uses them when needed.
BAN AI-ISMS. Sound human. Use specific, niche-relevant terminology. Use Roboto/Inter only.
If images are provided, derive the 'theme' colors from the images to ensure brand harmony."""
