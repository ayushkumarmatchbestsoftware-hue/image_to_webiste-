"""
Design derivation from a Product Spec — FR-7 (Genre), FR-8 (Grade), FR-9 (Skeleton).

This replaces the keyword-matching layer in core/utils.py. The old path read
the *text prompt* for a niche word and looked up a fixed palette; every design
decision was made before a pixel was seen. Here, all three decisions read the
Product Spec, which is derived from the photo.

  Genre     from spec.category. Fixes type, ornament level and motion BEFORE
            any colour is chosen. Each category maps to several candidate
            Genres so the same category does not produce the same Genre every
            time (FR-7), and Spin (FR-20) walks this list.

  Grade     from spec.dominant_colours, white-balance corrected first (FR-8),
            then contrast-repaired via the existing validate_and_fix_theme so
            WCAG AA holds no matter what came off the photo.

  Skeleton  from spec.geometry.orientation. A tall object and a wide object do
            not get the same composition (FR-9).
"""
import colorsys
import hashlib
from typing import Optional

from core.utils import (
    contrast_ratio, hex_to_rgb, is_dark, relative_luminance,
    rgba_from_hex, validate_and_fix_theme,
)

# ---------------------------------------------------------------------------
# FR-7  Visual Genre
# ---------------------------------------------------------------------------
# Each Genre fixes typography, ornament and motion. Colour is NOT set here —
# that is the Grade's job, and the PRD is explicit that Genre is chosen first.
VISUAL_GENRES = {
    "artisanal-textile": {
        "font_heading": "Fraunces", "font_body": "Roboto",
        "ornament": "high", "motion": "slow", "hero_style": "split-left",
        "card_style": "flat", "divider_style": "none",
        "register": "tactile, made-by-hand, material-led",
    },
    "editorial-apparel": {
        "font_heading": "Playfair Display", "font_body": "Roboto",
        "ornament": "low", "motion": "restrained", "hero_style": "fullbleed",
        "card_style": "flat", "divider_style": "none",
        "register": "spare, confident, fashion-editorial",
    },
    "appetite-food": {
        "font_heading": "Fraunces", "font_body": "Roboto",
        "ornament": "medium", "motion": "warm", "hero_style": "fullbleed",
        "card_style": "elevated", "divider_style": "wave",
        "register": "sensory, immediate, appetite-first",
    },
    "market-food": {
        "font_heading": "Outfit", "font_body": "Roboto",
        "ornament": "high", "motion": "lively", "hero_style": "bold-center",
        "card_style": "outlined", "divider_style": "diagonal",
        "register": "busy, generous, street-market energy",
    },
    "playful-toy": {
        "font_heading": "Outfit", "font_body": "Roboto",
        "ornament": "high", "motion": "bouncy", "hero_style": "bold-center",
        "card_style": "elevated", "divider_style": "wave",
        "register": "bright, direct, parent-reassuring",
    },
    "heritage-craft": {
        "font_heading": "Fraunces", "font_body": "Roboto",
        "ornament": "medium", "motion": "slow", "hero_style": "split-left",
        "card_style": "outlined", "divider_style": "none",
        "register": "lineage, technique, provenance",
    },
    "quiet-premium": {
        "font_heading": "Plus Jakarta Sans", "font_body": "Roboto",
        "ornament": "low", "motion": "restrained", "hero_style": "split-left",
        "card_style": "flat", "divider_style": "none",
        "register": "understated, precise, materials-first",
    },
}

# FR-7: at least two candidates per launch category.
CATEGORY_GENRES = {
    "apparel": ["artisanal-textile", "editorial-apparel", "heritage-craft"],
    "food":    ["appetite-food", "market-food"],
    "toys":    ["playful-toy", "heritage-craft"],
    "other":   ["quiet-premium", "editorial-apparel"],
}

# ---------------------------------------------------------------------------
# FR-9  Layout Skeleton
# ---------------------------------------------------------------------------
# Section order and hero treatment, keyed off the object's own geometry.
SKELETONS = {
    "tall": {
        "name": "tall-object",
        "hero_style": "split-left",
        "sections": ["hero", "about", "services", "portfolio", "faq", "contact"],
        "note": "vertical product sits beside copy rather than under it",
    },
    "wide": {
        "name": "wide-object",
        "hero_style": "fullbleed",
        "sections": ["hero", "stats", "services", "about", "portfolio", "contact"],
        "note": "horizontal product spans full width above the fold",
    },
    "square": {
        "name": "square-object",
        "hero_style": "bold-center",
        "sections": ["hero", "about", "services", "testimonials", "faq", "contact"],
        "note": "centred composition, product framed rather than bled",
    },
}


def _spin_index(spec: dict, spin: int, salt: str) -> int:
    """
    Deterministic-but-varied selection. Same Spec reproduces the same design
    (the NFR in §8), while Spin walks to a genuinely different one (FR-20).
    """
    seed = f"{spec.get('sub_type','')}|{spec.get('mood','')}|{salt}"
    base = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16)
    return base + spin


def select_visual_genre(spec: dict, spin: int = 0) -> dict:
    """FR-7: Genre from category, before any colour is chosen."""
    cands = CATEGORY_GENRES.get(spec.get("category", "other"), CATEGORY_GENRES["other"])
    name = cands[_spin_index(spec, spin, "genre") % len(cands)]
    genre = dict(VISUAL_GENRES[name])
    genre["name"] = name
    return genre


# ---------------------------------------------------------------------------
# Hero variants — where the product photo actually sits
# ---------------------------------------------------------------------------
# Every Pack previously opened with the photo at the top, so all output looked
# alike no matter which Pack won. These are real compositions, and two of them
# deliberately do NOT lead with the image: a page can open on its words and
# introduce the product further down, which is what a tall or low-resolution
# photo often wants anyway.
HERO_VARIANTS = {
    "side-right": {"leads_with_image": False, "fits": ("tall", "square"),
                   "note": "copy left, product right — the classic split"},
    "side-left":  {"leads_with_image": True,  "fits": ("tall", "square"),
                   "note": "product left, copy right"},
    "bleed":      {"leads_with_image": True,  "fits": ("wide", "square"),
                   "note": "full-bleed photo behind the headline"},
    "plate":      {"leads_with_image": True,  "fits": ("wide",),
                   "note": "full-width band above the words"},
    "below":      {"leads_with_image": False, "fits": ("tall", "wide", "square"),
                   "note": "words first; the product appears beneath them"},
    "inset":      {"leads_with_image": False, "fits": ("tall", "square"),
                   "note": "words dominate; a small framed product sits beside"},
}


def select_hero_variant(spec: dict, quality: Optional[dict] = None,
                        spin: int = 0) -> str:
    """
    Pick the hero composition from the object's geometry, filtered by what the
    photo can actually support, then rotated by Spin.

    A photo that cannot carry a full-bleed treatment (FR-12's resolution guard)
    never gets offered one, so the variant list here is already safe to choose
    from blindly.
    """
    orientation = (spec.get("geometry") or {}).get("orientation", "square")
    hero_capable = (quality or {}).get("hero_capable", True)

    usable = [name for name, v in HERO_VARIANTS.items()
              if orientation in v["fits"]]
    if not hero_capable:
        # Below threshold a big image only magnifies its own defects — and
        # "below" renders the photo FULL WIDTH, so it belonged on the unsafe
        # list rather than the safe one. Only the contained compositions
        # survive: they place the photo in a column, not across the page.
        usable = [n for n in usable if n in ("inset", "side-right", "side-left")]
    if not usable:
        usable = ["side-right"]

    idx = _spin_index(spec, spin, "hero") % len(usable)
    return usable[idx]


def select_skeleton(spec: dict, spin: int = 0) -> dict:
    """FR-9: Skeleton from object geometry, not from category."""
    orientation = (spec.get("geometry") or {}).get("orientation", "square")
    skel = dict(SKELETONS.get(orientation, SKELETONS["square"]))
    skel["orientation"] = orientation
    return skel


# ---------------------------------------------------------------------------
# FR-8  Grade
# ---------------------------------------------------------------------------
def estimate_illuminant(channel_means) -> tuple:
    """
    Grey-world illuminant estimate from the whole-frame channel means measured
    in vision.analyze_quality(). Returns per-channel gains that neutralise the
    cast when multiplied through.
    """
    try:
        r, g, b = [max(1e-6, float(c)) for c in channel_means]
    except Exception:
        return (1.0, 1.0, 1.0)
    grey = (r + g + b) / 3.0
    # Clamped so a genuinely warm-coloured product (terracotta, biryani) is not
    # bleached into neutrality — we are correcting the lamp, not the object.
    return tuple(min(1.35, max(0.75, grey / c)) for c in (r, g, b))


def white_balance_hex(hex_colour: str, gains: tuple) -> str:
    """Apply illuminant gains to one colour. FR-8: correct BEFORE extraction."""
    rgb = hex_to_rgb(hex_colour)
    if not rgb:
        return hex_colour
    out = [min(255, max(0, int(round(c * g)))) for c, g in zip(rgb, gains)]
    return "#{:02x}{:02x}{:02x}".format(*out)


def _hls(hex_colour: str):
    rgb = hex_to_rgb(hex_colour) or (128, 128, 128)
    r, g, b = [c / 255.0 for c in rgb]
    return colorsys.rgb_to_hls(r, g, b)


def _from_hls(h: float, l: float, s: float) -> str:
    l = min(1.0, max(0.0, l))
    s = min(1.0, max(0.0, s))
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return "#{:02x}{:02x}{:02x}".format(
        int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))


def _tone(hex_colour: str, lightness: float, saturation: float) -> str:
    """
    Re-tone a colour to an ABSOLUTE lightness/saturation, keeping only its hue.

    Absolute rather than relative on purpose: adding a delta to an already-light
    anchor saturates to white and to an already-dark one collapses to grey,
    which is how the page loses the product's hue entirely. Targeting the value
    means a pale cream and a deep indigo both yield a usable ground that still
    reads as *that product's* colour.
    """
    h, _l, _s = _hls(hex_colour)
    return _from_hls(h, lightness, saturation)


def _pick_anchor(colours: list, spin: int) -> str:
    """
    Choose the Grade's anchor from the product's own colours.

    Scores for chroma and for sitting near mid-lightness — a near-white or
    near-black sample carries no hue worth building a brand on, so it loses to
    any colour that does. A genuinely monochrome product falls through to a
    neutral and takes its interest from the Genre instead.
    """
    scored = []
    for c in colours:
        if not hex_to_rgb(c):
            continue
        h, l, s = _hls(c)
        # Penalise the extremes hard: at l<0.12 or l>0.88 there is no usable hue.
        extremity = abs(l - 0.5) / 0.5           # 0 at mid, 1 at either end
        usable = max(0.0, 1.0 - extremity ** 2)
        scored.append((s * usable, c))
    if not scored:
        return "#6b6b6b"
    scored.sort(reverse=True)
    # Only rotate among colours that actually carry hue, so Spin never lands
    # on the near-white sample and produces a washed-out Grade.
    strong = [c for score, c in scored if score > 0.06] or [scored[0][1]]
    return strong[spin % len(strong)]


def derive_grade(spec: dict, quality: Optional[dict] = None, spin: int = 0,
                 density: str = "generous", mode: str = "light") -> dict:
    """
    FR-8: build the Grade from the photo's own colours.

    White-balance first, then construct a full token set around one anchor,
    then hand the result to the existing validate_and_fix_theme() so the WCAG
    AA guarantee is enforced by the same code that already guards the text path.
    """
    gains = estimate_illuminant((quality or {}).get("channel_means", [1, 1, 1]))
    corrected = [white_balance_hex(c, gains) for c in spec.get("dominant_colours", [])]

    anchor = _pick_anchor(corrected, spin)
    a_hue, _al, a_sat = _hls(anchor)

    # Ground the page in a TINT of the product's own hue rather than flat white
    # — this is what stops every Site looking like the same template. The
    # saturation is deliberately low but never zero: enough that a cotton-indigo
    # page and a terracotta page are visibly different grounds, not enough to
    # fight the product photo sitting on top of them.
    tint = min(0.16, max(0.045, a_sat * 0.30))
    dark = (mode or "light").lower() == "dark"

    if dark:
        # A dark ground is not "light inverted": the tint has to be stronger to
        # survive at low lightness, and the text sits well short of pure white
        # so a bright product photo is the brightest thing on the page.
        bg = _tone(anchor, 0.085, min(0.34, max(0.10, a_sat * 0.55)))
        bg_alt = _tone(anchor, 0.135, min(0.30, max(0.09, a_sat * 0.48)))
        text_main = _tone(anchor, 0.93, min(0.10, a_sat * 0.16))
        text_muted = _tone(anchor, 0.66, min(0.14, a_sat * 0.22))
        # On a dark ground the brand colour has to be light enough to read
        # against it, which is the opposite of what it needs on paper.
        primary = _tone(anchor, 0.66, max(0.48, min(0.86, a_sat * 1.3)))
        primary_dark = _tone(anchor, 0.50, max(0.50, min(0.88, a_sat * 1.35)))
    else:
        bg = _tone(anchor, 0.965, tint)
        bg_alt = _tone(anchor, 0.925, min(0.20, tint * 1.35))
        # Primary has to survive as a button fill, so it is driven to a lightness
        # that holds white text, and its saturation floored so a muted product
        # still yields a usable brand colour.
        primary = _tone(anchor, 0.38, max(0.42, min(0.82, a_sat * 1.25)))
        primary_dark = _tone(anchor, 0.26, max(0.45, min(0.85, a_sat * 1.3)))
        # Text carries the hue at very low lightness — near-black, but warm or
        # cool in sympathy with the product rather than a generic #111.
        text_main = _tone(anchor, 0.13, min(0.30, max(0.10, a_sat * 0.45)))
        text_muted = _tone(anchor, 0.40, min(0.22, max(0.07, a_sat * 0.30)))

    # Accent: the next product colour that is genuinely a different hue,
    # otherwise a rotation around the wheel from the anchor.
    accent = None
    for c in corrected:
        if c == anchor:
            continue
        c_hue, _cl, c_sat = _hls(c)
        hue_gap = min(abs(c_hue - a_hue), 1.0 - abs(c_hue - a_hue))
        if hue_gap > 0.08 and c_sat > 0.15:
            accent = _tone(c, 0.46, max(0.45, c_sat))
            break
    if not accent:
        accent = _from_hls((a_hue + 0.5) % 1.0, 0.47, max(0.45, min(0.8, a_sat * 1.2)))

    genre = select_visual_genre(spec, spin)
    theme = {
        "primary": primary, "primary_dark": primary_dark,
        "bg": bg, "bg_alt": bg_alt,
        "text_main": text_main, "text_muted": text_muted,
        "accent": accent,
        "font_heading": genre["font_heading"], "font_body": genre["font_body"],
        "hero_style": genre["hero_style"], "card_style": genre["card_style"],
        "divider_style": genre["divider_style"],
    }

    # FR-10: Density Mode governs whitespace.
    if density == "dense":
        theme["radius_card"] = "4px"
        theme["radius_btn"] = "4px"
    else:
        theme["radius_card"] = "0px"
        theme["radius_btn"] = "0px"

    # Reuse the existing guard — this is what enforces WCAG AA and clamps every
    # enum. Fallback is the Grade itself, so a repair stays within the photo's
    # own palette wherever possible.
    # The fallback stays inside the product's own hue family so that a repair
    # by validate_and_fix_theme lands on a slightly safer version of THIS
    # Grade, not on a generic off-white that would erase the derivation.
    fallback = {
        "primary": primary, "primary_dark": primary_dark,
        "bg": _tone(anchor, 0.07 if dark else 0.975, min(0.10, tint)),
        "bg_alt": _tone(anchor, 0.12 if dark else 0.94, min(0.12, tint)),
        "text_main": _tone(anchor, 0.95 if dark else 0.11, 0.10),
        "text_muted": _tone(anchor, 0.62 if dark else 0.42, 0.08),
        "accent": accent,
        "font_heading": genre["font_heading"], "font_body": genre["font_body"],
        "hero_style": genre["hero_style"], "card_style": genre["card_style"],
        "divider_style": genre["divider_style"],
    }
    fixed = validate_and_fix_theme(theme, fallback, has_image=True)
    fixed["_mode"] = "dark" if dark else "light"
    fixed["_genre"] = genre["name"]
    fixed["_anchor"] = anchor
    fixed["_illuminant_gains"] = [round(g, 3) for g in gains]
    return fixed


def derive_design(spec: dict, quality: Optional[dict] = None, spin: int = 0,
                  density: str = "generous", mode: str = "light") -> dict:
    """One call producing all three FR-7/8/9 decisions for a Product Spec."""
    genre = select_visual_genre(spec, spin)
    skeleton = select_skeleton(spec, spin)
    grade = derive_grade(spec, quality, spin, density, mode)
    # Skeleton wins on hero treatment — geometry is a harder constraint than
    # genre preference, per FR-9.
    grade["hero_style"] = skeleton["hero_style"]
    if not (quality or {}).get("hero_capable", True) and grade["hero_style"] == "fullbleed":
        # FR-12: never place an image at a size its resolution cannot support.
        grade["hero_style"] = "split-left"
    hero_variant = select_hero_variant(spec, quality, spin)
    grade["_hero_variant"] = hero_variant
    return {"genre": genre, "skeleton": skeleton, "theme": grade,
            "layout": list(skeleton["sections"]), "density": density,
            "hero_variant": hero_variant,
            "leads_with_image": HERO_VARIANTS[hero_variant]["leads_with_image"]}
