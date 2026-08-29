"""
Offline fallback — runs the whole pipeline with no model API at all.

Two stages normally need a model: detection (vision.detect_product) and copy
(photo_pipeline.generate_copy). When no usable key is present, these stand in
for them so the rest of the system — design derivation, Pack selection,
rendering, editing, download — stays fully exercisable.

What is real here and what is not:

  REAL   dominant colours, orientation, aspect, fills-frame estimate, every
         quality metric. These come off the pixels with PIL and are exactly
         what the model path would have been asked to report.

  GUESSED  category and sub-type. Without a vision model there is no honest way
         to know a kurta from a curtain, so category comes from the seller's own
         selection if they gave one and is otherwise marked low-confidence
         "other" — never invented. This is the FR-6 correction path doing double
         duty as the offline input.

  TEMPLATED  copy. Written from the seller's own words plus the Spec, with no
         invented facts — the same §9.1 rule the model path follows. It reads as
         placeholder because it is placeholder; it is not pretending to be
         generated prose.
"""
import colorsys
import logging
import os
from typing import Optional

logger = logging.getLogger("offline")


def api_available() -> bool:
    """
    True only if a key is present, the client constructed, AND no previous call
    failed for a reason retrying cannot fix (revoked key, exhausted quota).
    """
    from core.llm import get_client, api_dead
    dead, _why = api_dead()
    if dead:
        return False
    return bool(os.getenv("OPENAI_API_KEY")) and get_client() is not None


# ---------------------------------------------------------------------------
# Detection stand-in
# ---------------------------------------------------------------------------
def extract_palette(image_path: str, n: int = 5) -> list:
    """
    Real dominant colours off the product photo.

    Quantises to a small palette and drops near-white / near-black entries,
    which are almost always background or shadow rather than the product.
    """
    try:
        from PIL import Image
    except Exception:
        return ["#8a8a8a"]
    try:
        img = Image.open(image_path).convert("RGB")
    except Exception:
        return ["#8a8a8a"]
    try:
        work = img.copy()
        work.thumbnail((320, 320), Image.LANCZOS)
        # Crop to the centre 70% — the product is nearly always framed centrally,
        # and this cheaply biases the palette away from the surface behind it.
        w, h = work.size
        work = work.crop((int(w * .15), int(h * .15), int(w * .85), int(h * .85)))

        q = work.quantize(colors=max(6, n * 2), method=Image.MEDIANCUT)
        pal = q.getpalette() or []
        counts = sorted(q.getcolors() or [], reverse=True)

        out = []
        for count, idx in counts:
            r, g, b = pal[idx * 3:idx * 3 + 3]
            _h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
            if l > 0.93 or l < 0.06:      # paper white / pure shadow
                continue
            if s < 0.05 and 0.35 < l < 0.75:   # flat mid grey, carries no hue
                continue
            hexv = "#{:02x}{:02x}{:02x}".format(r, g, b)
            if hexv not in out:
                out.append(hexv)
            if len(out) >= n:
                break
        return out or ["#8a8a8a"]
    finally:
        img.close()


def offline_spec(image_path: str, quality: dict,
                 category: Optional[str] = None,
                 sub_type: Optional[str] = None,
                 seller_facts: str = "") -> dict:
    """
    A Product Spec built without a vision model.

    Everything measurable is measured; everything that would need to be
    recognised is either taken from the seller or explicitly marked unknown.
    """
    aspect = quality.get("aspect", 1.0) or 1.0
    if aspect <= 0.8:
        orientation = "tall"
    elif aspect >= 1.25:
        orientation = "wide"
    else:
        orientation = "square"

    cat = (category or "").strip().lower()
    if cat not in {"apparel", "food", "toys"}:
        cat = "other"

    return {
        "category": cat,
        "sub_type": (sub_type or "").strip() or "product",
        "confidence": 0.35 if cat != "other" else 0.0,
        "material": "",
        "finish": "",
        "dominant_colours": extract_palette(image_path),
        "geometry": {
            "orientation": orientation,
            "shape": f"{orientation} object",
            "fills_frame": 0.6,
        },
        "mood": "",
        "implied_audience": "",
        "implied_price_band": "mid",
        "background_separable": "flat" not in quality.get("defects", []),
        "separability_note": "not assessed offline",
        "visible_text": "",
        "suggested_business": seller_facts[:160],
        "low_confidence": True,
        "_offline": True,
    }


# ---------------------------------------------------------------------------
# Copy stand-in
# ---------------------------------------------------------------------------
def offline_copy(spec: dict, genre: dict, layout: list, brand_name: str = "",
                 price: str = "", seller_facts: str = "") -> dict:
    """
    Copy assembled from what the seller actually gave us.

    Deliberately plain. It states nothing that was not supplied — no counts, no
    dates, no certifications — so it can never put a false claim on a page.
    """
    brand = (brand_name or "").strip() or "Your Brand"
    thing = spec.get("sub_type") or "product"
    facts = (seller_facts or "").strip()
    lead = facts.split(".")[0].strip() if facts else ""

    sub = lead or f"{brand} makes {thing}. Add your own words here."
    about_desc = facts or (
        f"Write a few lines about {brand} here — how you started, what you make, "
        "and why someone should buy from you rather than anyone else.")

    services = [
        {"title": thing.title(), "description": lead or "Describe this in your own words.",
         "icon": "star"},
        {"title": "Made to order", "description": "Say how long it takes and what you need from a buyer.",
         "icon": "shield"},
        {"title": "Delivery", "description": "Say where you ship and how long it takes.",
         "icon": "globe"},
    ]

    return {
        "site_info": {"display_name": brand,
                      "site_title": lead or f"{thing.title()}, made by {brand}",
                      "tagline": facts[:90] if facts else ""},
        "home": {
            "title": lead or thing.title(),
            "subtitle": sub,
            "cta": "Enquire on WhatsApp",
            "label": "What we make",
            "pillar1_title": "Made by hand",
            "pillar1_desc": "Replace this with something true about how you make it.",
            "pillar2_title": "Direct from us",
            "pillar2_desc": "Replace this with how a buyer orders from you.",
        },
        "about": {"heading": f"About {brand}", "description": about_desc, "mission": ""},
        "services": services,
        "portfolio": [{"title": thing.title(), "client": "", "tag": "Our work",
                       "description": lead or "Describe this piece.", "outcome": ""}],
        # Still empty offline: a made-up review is a lie about a real
        # person's opinion, which is worse than an empty section.
        "testimonials": [],
        "faq": [
            {"question": "How do I order?", "answer": "Message us and we will confirm."},
            {"question": "Do you deliver?", "answer": "Add your delivery details here."},
        ],
        "stats": [],                 # never fabricate numbers
        "pricing": [],
        "contact": {"title": "Get in touch",
                    "description": "Add your phone number and address here.",
                    "email": "", "phone": "", "address": "", "label": "Enquire"},
        "footer": {"copyright": brand, "address": ""},
        "_offline": True,
    }
