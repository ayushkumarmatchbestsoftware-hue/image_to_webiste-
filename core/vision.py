"""
Photo intake: Triage (FR-2, FR-3) and Detection (FR-5).

This is the stage the PRD calls the highest-leverage operation in the product,
and the split here follows it literally:

  Triage      cheap, local, deterministic. Runs on pixels with PIL only — no
              network — so it clears the FR-2 "verdict in under 2 seconds"
              requirement by construction rather than by hoping the model is
              quick. It measures the things that actually cap a page's beauty
              ceiling: exposure, colour cast, focus, and effective resolution.

  Detection   one vision call that emits the Product Spec. Per FR-5 / §4.2 this
              is the ONLY stage that ever looks at the Source Photo; every
              downstream stage reads the Spec instead. A malformed Spec halts
              generation rather than degrading it.

Coaching text (FR-3) needs the category, which only Detection knows, so the
public entry point `intake()` runs quality first, then detection, then combines
them into one verdict. Quality alone still returns instantly for the UI to show
while detection is in flight.
"""
import logging
import math
from typing import Optional

from core.llm import chat_json, image_part, MODEL_FAST, supports_vision

logger = logging.getLogger("vision")

# FR-5: the launch categories. Anything else returns low confidence rather
# than a guess.
LAUNCH_CATEGORIES = {"apparel", "food", "toys"}

# Thresholds. Deliberately generous — the PRD's SM-4 target is only 50% of
# fail-verdict Sellers retaking, and over-rejecting a usable photo is a worse
# failure than letting a mediocre one through with a warning.
MIN_EDGE_PX = 640          # below this, no full-bleed hero is possible (FR-12)
GOOD_EDGE_PX = 1200
DARK_MEAN = 55             # 0-255
BRIGHT_MEAN = 225
LOW_CONTRAST_STD = 28
BLUR_VAR = 90              # Laplacian-proxy variance
CAST_RATIO = 1.28          # max/min channel mean ratio


def analyze_quality(path: str) -> dict:
    """
    Pure-PIL measurement of a Source Photo. No network, no model.

    Returns raw metrics plus a list of named defects. Never raises — an
    undecodable file is itself a fail verdict.
    """
    try:
        from PIL import Image, ImageFilter, ImageStat
    except Exception as e:
        return {"ok": False, "defects": ["unreadable"], "error": str(e)}

    try:
        img = Image.open(path)
        img = img.convert("RGB")
    except Exception as e:
        return {"ok": False, "defects": ["unreadable"], "error": str(e)}

    try:
        w, h = img.size
        long_edge, short_edge = max(w, h), min(w, h)

        # Work on a bounded copy — these statistics are scale-stable and a
        # 12MP original costs real time to filter.
        work = img.copy()
        work.thumbnail((1024, 1024), Image.LANCZOS)

        stat = ImageStat.Stat(work)
        r_mean, g_mean, b_mean = stat.mean
        mean = (r_mean + g_mean + b_mean) / 3.0
        std = sum(stat.stddev) / 3.0

        # Colour cast: how far apart the channel means are. A tungsten-lit
        # photo pushes red well above blue. Used both as a defect and, in
        # design.py, as the illuminant estimate for white balance (FR-8).
        lo = max(1e-6, min(r_mean, g_mean, b_mean))
        cast_ratio = max(r_mean, g_mean, b_mean) / lo

        # Focus: variance of an edge-detected greyscale copy. Not a true
        # Laplacian, but FIND_EDGES is the same family and ships with PIL.
        grey = work.convert("L")
        edges = grey.filter(ImageFilter.FIND_EDGES)
        focus_var = ImageStat.Stat(edges).stddev[0] ** 2

        defects = []
        if long_edge < MIN_EDGE_PX:
            defects.append("resolution")
        if mean < DARK_MEAN:
            defects.append("dark")
        elif mean > BRIGHT_MEAN:
            defects.append("blown")
        if std < LOW_CONTRAST_STD:
            defects.append("flat")
        if cast_ratio > CAST_RATIO:
            defects.append("cast")
        if focus_var < BLUR_VAR:
            defects.append("soft")

        return {
            "ok": True,
            "width": w, "height": h,
            "long_edge": long_edge, "short_edge": short_edge,
            "aspect": round(w / h, 3) if h else 1.0,
            "mean": round(mean, 1),
            "std": round(std, 1),
            "cast_ratio": round(cast_ratio, 3),
            "channel_means": [round(r_mean, 1), round(g_mean, 1), round(b_mean, 1)],
            "focus_var": round(focus_var, 1),
            "defects": defects,
            # Effective resolution drives FR-12's treatment choice.
            "hero_capable": long_edge >= GOOD_EDGE_PX,
        }
    finally:
        img.close()


# --- FR-3: category-aware coaching --------------------------------------
# Two sentences maximum, naming one action, per the PRD's own constraint.
_COACHING = {
    "food": {
        "dark":       "Shoot it near a window in daylight, not under tube light.",
        "cast":       "That yellow tint is the tube light. Shoot near a window instead.",
        "soft":       "Tap the dish on your screen to focus, then hold still.",
        "flat":       "Move the plate onto a plain surface so the food stands out.",
        "blown":      "Move out of direct sun — the highlights are burning out.",
        "resolution": "Use your main camera rather than a screenshot or forward.",
        "_angle":     "Shoot from about 45 degrees, plain surface underneath.",
    },
    "apparel": {
        "dark":       "Lay it near a window in daylight — indoor light is too dim.",
        "cast":       "Room light is tinting the fabric colour. Try daylight.",
        "soft":       "Tap the garment on screen to focus before you shoot.",
        "flat":       "Use a plain bedsheet or wall — the busy background hides it.",
        "blown":      "Step out of direct sun so the fabric keeps its texture.",
        "resolution": "Shoot fresh with your main camera rather than reusing a small file.",
        "_angle":     "Flat-lay it on a plain sheet, or hang it straight, shot square on.",
    },
    "toys": {
        "dark":       "Move to a brighter spot — daylight from a window works best.",
        "cast":       "The light is tinting the colours. Try near a window.",
        "soft":       "Tap the toy on screen to focus, then hold still.",
        "flat":       "Put it on a plain surface so the shape reads clearly.",
        "blown":      "Move out of direct sun — the colours are washing out.",
        "resolution": "Use your main camera rather than a screenshot.",
        "_angle":     "Shoot at the toy's own eye level, plain surface behind.",
    },
}
_GENERIC = {
    "dark":       "Move somewhere brighter — daylight near a window works best.",
    "cast":       "The light is tinting the colours. Try shooting near a window.",
    "soft":       "Tap the product on screen to focus, then hold still.",
    "flat":       "Put it on a plain surface so the product separates from the background.",
    "blown":      "Move out of direct sunlight — the highlights are burning out.",
    "resolution": "Shoot fresh with your main camera rather than reusing a small image.",
    "unreadable": "That file could not be opened. Try a JPEG or PNG from your camera.",
    "_angle":     "Shoot from about 45 degrees on a plain surface.",
}


def build_guidance(defects: list, category: Optional[str]) -> str:
    """FR-3: at most two sentences, naming one action, specific to category."""
    if not defects:
        return ""
    table = _COACHING.get((category or "").lower(), _GENERIC)
    primary = defects[0]
    first = table.get(primary) or _GENERIC.get(primary) or _GENERIC["_angle"]
    second = table.get("_angle", _GENERIC["_angle"])
    if first == second:
        return first
    return f"{first} {second}"


DETECTION_SYSTEM = """You are a product analyst. You are shown ONE photograph of a single physical product that a small seller wants to build a website around.

Return a JSON object with EXACTLY this shape:
{
  "category": "apparel|food|toys|other",
  "sub_type": "specific noun, e.g. 'block-print cotton kurta', 'chicken biryani', 'wooden pull-along duck'",
  "confidence": 0.0-1.0,
  "material": "what it is made of, as visible",
  "finish": "surface quality, e.g. 'matte handloom weave', 'glossy glaze'",
  "dominant_colours": ["#rrggbb", "#rrggbb", "#rrggbb"],
  "geometry": {"orientation": "tall|wide|square", "shape": "short phrase", "fills_frame": 0.0-1.0},
  "mood": "two or three adjectives",
  "implied_audience": "who buys this",
  "implied_price_band": "budget|mid|premium|luxury",
  "background_separable": true|false,
  "separability_note": "why the product is or is not cleanly separable from its background",
  "visible_text": "any text legible on the product itself, else empty string",
  "suggested_business": "one sentence describing the business that sells this, in the seller's likely words",
  "estimated_price_inr": 4999,
  "estimated_price_usd": 60,
  "staging_scene_prompt": "a vivid 1-sentence prompt describing the ideal photorealistic commercial setting for this product (e.g. 'A premium English willow cricket bat resting against an old wooden cricket pavilion fence on lush green turf with a cricket ball and batting pads softly blurred in the background, warm natural sunlight')"
}

RULES:
- category MUST be "other" if it is not clearly apparel, food, or a toy. Never guess to force a fit.
- confidence reflects how certain you are of category AND sub_type together.
- dominant_colours are sampled from the PRODUCT itself, not the background or the lighting.
- estimated_price_inr and estimated_price_usd should be realistic market retail prices as integers.
- staging_scene_prompt MUST describe a beautiful, highly contextual commercial environment where this product naturally belongs.
- If the photo shows several products, describe the single most prominent one."""


async def detect_product(image_path: str) -> Optional[dict]:
    """
    FR-5: derive the Product Spec. The only stage that reads the Source Photo.

    Returns None on failure — the caller halts generation rather than
    proceeding on a malformed Spec.
    """
    if not supports_vision():
        # A text-only provider cannot look at the Source Photo. Say so plainly
        # rather than sending an image it will ignore and trusting the answer.
        logger.warning("provider has no vision support — detection unavailable")
        return None

    spec = await chat_json(
        system=DETECTION_SYSTEM,
        text="Analyse this product photograph and return the JSON object.",
        images=[image_path],
        model=MODEL_FAST,
        temperature=0.2,
        max_tokens=1200,
    )
    if not spec:
        return None

    # Validate the contract. A Spec missing its load-bearing fields is
    # malformed, and FR-5 says that halts rather than degrades.
    required = ("category", "sub_type", "dominant_colours", "geometry")
    missing = [k for k in required if not spec.get(k)]
    if missing:
        logger.error(f"Product Spec malformed — missing {missing}")
        return None

    cat = str(spec.get("category", "")).lower().strip()
    spec["category"] = cat if cat in LAUNCH_CATEGORIES else "other"
    try:
        spec["confidence"] = float(spec.get("confidence", 0.0))
    except (TypeError, ValueError):
        spec["confidence"] = 0.0
    # FR-5: outside the launch categories, say so explicitly.
    spec["low_confidence"] = spec["category"] == "other" or spec["confidence"] < 0.55

    colours = [c for c in spec.get("dominant_colours", [])
               if isinstance(c, str) and c.startswith("#") and len(c) in (4, 7)]
    spec["dominant_colours"] = colours or ["#8a8a8a"]

    geo = spec.get("geometry") or {}
    if geo.get("orientation") not in ("tall", "wide", "square"):
        geo["orientation"] = "square"
    spec["geometry"] = geo
    return spec


async def intake(image_path: str) -> dict:
    """
    Full FR-2 + FR-3 + FR-5 intake for one Source Photo.

    Returns:
      verdict   pass | warn | fail
      quality   the raw local metrics
      spec      the Product Spec, or None if detection failed
      guidance  category-aware coaching, empty on a pass
    """
    quality = analyze_quality(image_path)

    if not quality.get("ok"):
        return {"verdict": "fail", "quality": quality, "spec": None,
                "guidance": _GENERIC["unreadable"], "defects": ["unreadable"]}

    spec = await detect_product(image_path)
    category = (spec or {}).get("category")

    # Detection failing is not the same as the photo being fine. Without a
    # Product Spec there is nothing for the design stages to read (§4.2), so
    # this is a fail verdict in its own right rather than a silent pass.
    if spec is None:
        return {"verdict": "fail", "quality": quality, "spec": None,
                "defects": ["detection_failed"],
                "guidance": "Could not read the product in that photo. "
                            "Try a clearer shot on a plain surface."}

    defects = list(quality.get("defects", []))
    # The model can see one thing PIL cannot: whether the product actually
    # separates from what is behind it (FR-2 lists it as a triage dimension).
    if spec and spec.get("background_separable") is False and "flat" not in defects:
        defects.append("flat")

    # fail on a hard blocker, warn on anything else, pass on nothing.
    hard = {"unreadable", "resolution", "dark", "blown"}
    if any(d in hard for d in defects):
        verdict = "fail"
    elif defects:
        verdict = "warn"
    else:
        verdict = "pass"

    return {
        "verdict": verdict,
        "quality": quality,
        "spec": spec,
        "defects": defects,
        "guidance": build_guidance(defects, category),
    }


def get_default_price(spec: Optional[dict], currency: str = "INR") -> str:
    """
    Derive a market-appropriate default retail price when the seller did not supply one.
    """
    if not spec:
        return "₹4,999.00" if currency == "INR" else "$49.99"

    # 1. Try estimated price from vision model
    p_inr = spec.get("estimated_price_inr")
    p_usd = spec.get("estimated_price_usd")
    
    if currency == "INR" and p_inr:
        try:
            val = float(p_inr)
            return f"₹{val:,.2f}"
        except (ValueError, TypeError):
            pass
    elif currency == "USD" and p_usd:
        try:
            val = float(p_usd)
            return f"${val:,.2f}"
        except (ValueError, TypeError):
            pass

    # 2. Smart category & band fallback
    band = str(spec.get("implied_price_band", "")).lower()
    cat = str(spec.get("category", "")).lower()
    sub = str(spec.get("sub_type", "")).lower()

    if "bat" in sub or "cricket" in sub:
        base = 4999.0
    elif "watch" in sub or "jewel" in sub:
        base = 6999.0 if band in ("premium", "luxury") else 2999.0
    elif "shoe" in sub or "boot" in sub:
        base = 3499.0
    elif "bag" in sub or "leather" in sub:
        base = 3999.0
    elif cat == "apparel":
        base = 1999.0 if band in ("premium", "luxury") else 999.0
    elif cat == "food":
        base = 499.0
    elif cat == "toys":
        base = 799.0
    else:
        base = 2499.0 if band in ("premium", "luxury") else 1499.0

    if currency == "INR":
        return f"₹{base:,.2f}"
    elif currency == "USD":
        return f"${(base / 80):,.2f}"
    elif currency == "EUR":
        return f"€{(base / 88):,.2f}"
    elif currency == "GBP":
        return f"£{(base / 105):,.2f}"
    return f"₹{base:,.2f}"

