"""
Template Packs — whole hand-built designs, chosen from the Product Spec.

The PRD's §10 objection to the existing renderer is that one skeleton with
swappable blocks produces *variations of one page*: two sellers in the same
category get the same layout with different words. A Pack is a genuinely
different design — its own HTML structure, its own CSS, its own type — so a
toy site and a running-gear site are not the same page wearing new colours.

Selection reads the Product Spec (never the photo, never the prompt), scoring
each Pack on category fit, sub-type keywords, and mood. The winner's own visual
identity is preserved; the derived Grade is applied only as a light accent
overlay so the page still carries the product's colour without the Pack losing
what makes it that Pack.

    spec ──▶ select_pack() ──▶ slug ──▶ templates/packs/<slug>/home.html
                                        + /packs/<slug>/css, images, fonts
"""
import hashlib
import json
import logging
import os
import re
from typing import Optional

# The Spec's value for "no category I recognise". It is deliberately NOT a key
# a Pack can claim a weight for - see score_packs.
UNCATEGORISED = "other"

logger = logging.getLogger("packs")

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
# categories : Product Spec categories this Pack is a natural fit for
# keywords   : sub_type / mood words that pull strongly toward this Pack
# against    : words that should push a product AWAY from this Pack
# Empty until templates are added. Each entry describes ONE design; the shape
# below is the whole contract, and adding a design means adding an entry here
# plus the two files named at the bottom of this comment.
#
#   "myslug": {
#       "title":     "Human name",
#       "source":    "where it came from",
#       "character": "one line on how it looks — shown in /packs-info",
#       "accent":    "#rrggbb",     signature colour, survives the derived Grade
#       "ink":       "#rrggbb",     default text colour
#       "paper":     "#rrggbb",     default ground
#       "heading_font": "CSS font stack",
#       "categories": {"toys": 3.0, "food": 0.5, "apparel": 1.0},
#       "keywords":   {"word": weight, ...},   whole-word matches that pull toward it
#       "against":    {"word": weight, ...},   words that push away from it
#       "sections":   ["hero", "about", "services", "portfolio", "contact"],
#   },
#
# Files each design must provide:
#   templates/packs/<slug>/_shell.html   <head>, styles, header, footer,
#                                        and {% block main %}{% endblock %}
#   templates/packs/<slug>/home.html     {% extends %} that shell, fills main
#
# It gets for free: the four shared sub-pages in templates/packs/_pages/, the
# six hero compositions in _pages/_hero.html, social meta, and Share Cards.
# A design whose CSS does not cover the shared pages' class names must ship its
# own sub-pages instead — mixing the two is what broke the last attempt.
BUILTIN_PACKS = {
    "noir": {
        "title": "Noir",
        "source": "original",
        "character": "DARK. One expensive object in a pool of light. Cormorant "
                     "Garamond tracked wide on near-black, centred, unhurried",
        "mode": "dark",
        "accent": "#c8a86b",
        "ink": "#f2efe9",
        "paper": "#0e0d0c",
        "heading_font": "'Cormorant Garamond', Georgia, serif",
        "use_case": "jewellery, watches, perfume, anything sold on desire",
        "categories": {"apparel": 2.0, "food": 0.6, "toys": 0.3},
        "keywords": {
            "jewellery": 3.0, "jewelry": 3.0, "ring": 2.5, "necklace": 3.0,
            "earring": 3.0, "bracelet": 2.5, "gold": 2.5, "silver": 2.5,
            "diamond": 3.0, "gemstone": 3.0, "watch": 3.0, "perfume": 3.0,
            "fragrance": 3.0, "silk": 2.0, "cashmere": 2.0, "luxury": 3.0,
            "heirloom": 2.5, "couture": 3.0, "bespoke": 2.5, "elegant": 2.0,
        },
        "against": {"budget": 2.5, "bulk": 2.0, "cute": 2.0},
        "sections": ["hero", "about", "portfolio", "services", "contact"],
    },
    "pulse": {
        "title": "Pulse",
        "source": "original",
        "character": "a release slip. Archivo Black shouting over Space Mono "
                     "spec rows on concrete, product pinned to a paper panel, "
                     "a perforated tear-edge carrying the lot and drop",
        "mode": "light",
        "accent": "#ff4d3d",
        "ink": "#f4f4f5",
        "paper": "#0d0f12",
        "heading_font": "'Archivo Black', 'Arial Black', sans-serif",
        "use_case": "streetwear, sneakers, gadgets, gear, anything with a drop",
        "categories": {"apparel": 2.4, "toys": 1.2, "food": 0.5},
        "keywords": {
            "sneaker": 3.0, "streetwear": 3.0, "hoodie": 3.0, "tshirt": 2.5,
            "tee": 2.5, "cap": 2.5, "jersey": 2.5, "merch": 3.0,
            "skate": 3.0, "board": 2.0, "gaming": 3.0, "console": 2.5,
            "headphone": 3.0, "speaker": 2.5, "gadget": 3.0, "device": 2.5,
            "drop": 2.5, "limited": 2.5, "bold": 2.0, "loud": 2.5,
            "athletic": 2.5, "gym": 2.5, "performance": 2.5, "energy": 2.0,
        },
        "against": {"heirloom": 2.5, "delicate": 2.0, "understated": 2.5},
        "sections": ["hero", "services", "portfolio", "about", "contact"],
    },
    "counter": {
        "title": "Counter",
        "source": "original",
        "character": "a menu board — condensed uppercase on a dark plate, items "
                     "as dot-leader rows running to a right-aligned price",
        "accent": "#e8a33d",
        "ink": "#1a1614",
        "paper": "#fbf7f0",
        "heading_font": "'Oswald', 'Arial Narrow', sans-serif",
        "mode": "light",
        "use_case": "food sold over a counter — stalls, cafes, tiffin, sweets",
        "categories": {"food": 3.0, "apparel": 0.6, "toys": 0.6},
        "keywords": {
            "biryani": 3.0, "curry": 2.5, "thali": 3.0, "snack": 2.5,
            "chaat": 3.0, "samosa": 3.0, "roll": 2.0, "kebab": 2.5,
            "bakery": 2.5, "bread": 2.5, "cake": 2.5, "sweet": 2.5,
            "mithai": 3.0, "coffee": 2.5, "chai": 3.0, "juice": 2.5,
            "tiffin": 3.0, "meal": 2.5, "menu": 3.0, "kitchen": 2.5,
            "restaurant": 2.5, "cafe": 2.5, "stall": 3.0, "counter": 2.5,
        },
        "against": {"understated": 1.5},
        "sections": ["hero", "services", "portfolio", "about", "contact"],
    },
    "binder": {
        "title": "Binder",
        "source": "adapted from a supplied Atelier & Co design",
        "character": "a dark spine of vertical tabs down the left edge with the "
                     "content on a paper sheet lifted off it; Fraunces over "
                     "Inter, Space Mono labels, one ink ledger strip",
        "mode": "light",
        "accent": "#b8863b",
        "ink": "#16231f",
        "paper": "#efe9da",
        "heading_font": "'Fraunces', Georgia, serif",
        "use_case": "studios and makers who sell a craft as much as a product",
        "categories": {"toys": 1.6, "apparel": 1.6, "food": 0.9},
        "keywords": {
            "studio": 3.0, "atelier": 3.0, "workshop": 2.5, "maker": 3.0,
            "craft": 2.5, "handmade": 2.0, "artisan": 2.5, "bespoke": 3.0,
            "commission": 3.0, "custom": 2.5, "furniture": 2.5, "joinery": 2.5,
            "ceramic": 2.0, "pottery": 2.5, "leather": 2.0, "wood": 2.0,
            "wooden": 2.0, "tool": 2.0, "hardware": 2.0, "restoration": 2.5,
            "portfolio": 2.5, "design": 2.0, "print": 2.0, "bindery": 3.0,
        },
        "against": {"bulk": 1.5, "wholesale": 1.0},
        "sections": ["hero", "services", "portfolio", "about", "contact"],
    },
    "tumble": {
        "title": "Tumble",
        "source": "adapted from the Tumble & Co. design supplied by the user",
        "character": "dotted cream ground, 3px ink borders on everything, hard "
                     "offset shadows, Baloo 2 rounded display, a 2x2 rotated "
                     "tile hero and a reviews carousel",
        "mode": "light",
        "accent": "#ffb020",
        "ink": "#241b2e",
        "paper": "#fff7e9",
        "heading_font": "'Baloo 2', 'Segoe UI', sans-serif",
        "use_case": "family shops selling a small range - toys, kids goods, "
                    "games, party and craft supplies",
        "categories": {"toys": 2.8, "food": 1.2, "apparel": 1.2},
        "keywords": {
            "toy": 3.0, "game": 2.5, "puzzle": 3.0, "block": 2.5,
            "plush": 2.5, "doll": 2.5, "rattle": 2.5, "stacker": 3.0,
            "kids": 3.0, "children": 3.0, "child": 2.5, "toddler": 3.0,
            "baby": 2.5, "nursery": 2.5, "playroom": 3.0, "play": 2.0,
            "craft": 2.0, "sticker": 2.5, "crayon": 2.5, "colouring": 2.5,
            "party": 2.5, "birthday": 2.5, "wooden": 2.0, "playful": 2.0,
        },
        "against": {"luxury": 2.5, "understated": 2.0, "heirloom": 1.5},
        "sections": ["hero", "services", "about", "portfolio", "contact"],
    },
    "bloom": {
        "title": "Bloom",
        "source": "original",
        "character": "a swatch card - calico ground, the sample pinned to board with "
                     "a selvedge notch down one edge, Petrona over Karla, "
                     "and a sewn care label carrying the real composition",
        "accent": "#e6a4b4",
        "ink": "#2a2226",
        "paper": "#fdfaf8",
        "heading_font": "'Fraunces', Georgia, serif",
        "mode": "light",
        "use_case": "soft goods and gifting — toys, knits, candles, babywear",
        "categories": {"toys": 2.6, "apparel": 1.6, "food": 1.2},
        "keywords": {
            "toy": 2.5, "doll": 3.0, "plush": 3.0, "soft": 2.5,
            "baby": 3.0, "child": 2.5, "children": 2.5, "kids": 2.5,
            "gift": 2.5, "hamper": 2.0, "candle": 2.5, "soap": 2.5,
            "skincare": 2.5, "balm": 2.5, "flower": 3.0, "floral": 2.5,
            "knit": 2.5, "crochet": 3.0, "embroidery": 2.5, "handmade": 2.0,
            "cute": 2.5, "playful": 2.0, "warm": 1.8, "cosy": 2.5, "cozy": 2.5,
        },
        "against": {"machined": 2.0, "precision": 1.5, "engineered": 2.0},
        "sections": ["hero", "services", "about", "portfolio", "contact"],
    },
}

# ── Discovery ────────────────────────────────────────────────────────────────
#
# A design describes itself. Drop a folder into templates/packs/ holding
# _shell.html, home.html and a pack.json, and it becomes available — no code
# change, no redeploy of anything but the templates.
#
# The dictionary above is the fallback, and it stays for a reason: if a
# manifest is missing or malformed, that design keeps working on its built-in
# definition rather than vanishing from the product. A design silently
# disappearing is a far worse failure than a stale description.

PACKS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "templates", "packs")

# Fields a manifest may set. Anything else is ignored rather than trusted —
# these values drive scoring and rendering, so an unexpected key is a mistake,
# not a feature.
MANIFEST_FIELDS = {
    "title", "source", "character", "mode", "accent", "ink", "paper",
    "heading_font", "use_case", "categories", "keywords", "against", "sections",
}
REQUIRED = {"title", "character", "sections"}


def _read_manifest(slug: str, folder: str):
    """Load one pack.json. Returns None and says why if it cannot be used."""
    path = os.path.join(folder, "pack.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as e:
        logger.warning(f"pack '{slug}': pack.json unreadable ({e}); "
                       "using the built-in definition")
        return None

    body = {k: v for k, v in raw.items() if k in MANIFEST_FIELDS}
    missing = REQUIRED - set(body)
    if missing:
        logger.warning(f"pack '{slug}': pack.json missing {sorted(missing)}; "
                       "using the built-in definition")
        return None
    # Scoring tables must be name -> number, or select_pack throws mid-generation
    # rather than here where it can be reported.
    for table in ("categories", "keywords", "against"):
        val = body.get(table)
        if val is not None and not (isinstance(val, dict) and all(
                isinstance(v, (int, float)) for v in val.values())):
            logger.warning(f"pack '{slug}': '{table}' must map names to numbers; "
                           "using the built-in definition")
            return None
    # A Pack cannot claim the uncategorised case for itself. Left in, it would
    # be silently ignored by the scorer, so say so rather than let an author
    # tune a number that does nothing.
    if UNCATEGORISED in (body.get("categories") or {}):
        logger.warning(f"pack '{slug}': '{UNCATEGORISED}' is not a category and "
                       "is ignored - products with no category are decided by "
                       "'keywords' and by the photo")
        body["categories"] = {k: v for k, v in body["categories"].items()
                              if k != UNCATEGORISED}

    if not isinstance(body.get("sections"), list) or not body["sections"]:
        logger.warning(f"pack '{slug}': 'sections' must be a non-empty list; "
                       "using the built-in definition")
        return None
    body.setdefault("mode", "light")
    return body


def discover(directory: str = None) -> dict:
    """
    Every design installed on disk.

    A folder counts only if it can actually render — _shell.html and home.html
    both present. Its definition comes from pack.json, or from the built-in
    registry when there is no manifest.
    """
    found = {}
    root = directory or PACKS_DIR
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return dict(BUILTIN_PACKS)

    for slug in entries:
        folder = os.path.join(root, slug)
        if slug.startswith(("_", ".")) or not os.path.isdir(folder):
            continue
        if not all(os.path.exists(os.path.join(folder, f))
                   for f in ("_shell.html", "home.html")):
            continue
        body = _read_manifest(slug, folder) or BUILTIN_PACKS.get(slug)
        if not body:
            logger.warning(f"pack '{slug}': no pack.json and no built-in "
                           "definition; skipped")
            continue
        found[slug] = dict(body)

    for slug in BUILTIN_PACKS:
        if slug not in found:
            logger.info(f"pack '{slug}': no folder on disk, not installed")
    return found


PACKS = discover()

if PACKS:
    logger.info(f"designs installed: {', '.join(sorted(PACKS))}")
else:
    logger.error("no designs found under templates/packs/")


DEFAULT_PACK = "binder"


def _haystack(spec: dict) -> str:
    """Everything in the Spec that carries meaning about what this thing is."""
    parts = [
        spec.get("sub_type", ""), spec.get("material", ""), spec.get("finish", ""),
        spec.get("mood", ""), spec.get("implied_audience", ""),
        spec.get("suggested_business", ""), spec.get("visible_text", ""),
    ]
    return " ".join(str(p) for p in parts).lower()


def _hits(text: str, table: dict) -> list:
    """
    Whole-word keyword matches only.

    Bare substring matching silently fires on fragments inside ordinary words —
    "art" inside "e-art-hy", "bar" inside "barber", "ai" inside "Jaipur" — and a
    phantom 2.5 is enough to hand the wrong design to a product. Anchoring on
    word boundaries costs nothing and removes the whole class of error. Multi-word
    keys still work because \b sits either side of the phrase.
    """
    out = []
    for kw, weight in table.items():
        if re.search(rf"\b{re.escape(kw)}\b", text):
            out.append((kw, weight))
    return out


def identity_accent(slug: str) -> Optional[str]:
    """
    The Pack's own signature colour, taken from the stylesheet it came from.

    The derived Grade governs the page's ground and text so the Site still
    carries the product's colour, but each Pack keeps one recognisable accent
    of its own — otherwise every Pack collapses into the same palette and the
    only thing distinguishing them is layout.
    """
    return (PACKS.get(slug) or {}).get("accent")


def _tiebreak(spec: dict) -> dict:
    """
    Nudges for a product no keyword recognises.

    Without this, anything scoring on category alone lands on the same Pack
    every time by a fixed 0.1 margin — which is a default dressed up as a
    decision. These read the product's own measured qualities instead, so an
    unrecognised bright object and an unrecognised muted one diverge.
    """
    import colorsys
    from core.utils import hex_to_rgb

    sat = light = 0.0
    cols = [c for c in (spec.get("dominant_colours") or []) if hex_to_rgb(c)]
    if cols:
        vals = []
        for c in cols[:3]:
            r, g, b = [x / 255.0 for x in hex_to_rgb(c)]
            _h, l, s = colorsys.rgb_to_hls(r, g, b)
            vals.append((l, s))
        light = sum(v[0] for v in vals) / len(vals)
        sat = sum(v[1] for v in vals) / len(vals)

    band = (spec.get("implied_price_band") or "").lower()
    orient = (spec.get("geometry") or {}).get("orientation", "square")

    n = {k: 0.0 for k in PACKS}
    # Saturated and bright reads playful; muted and mid reads heritage.
    # A saturated, bright product suits the soft and the menu-board designs;
    # a muted one suits the two austere designs. These only decide products no
    # keyword recognised, so they must read something real off the photo.
    n["bloom"] += (sat - 0.34) * 2.0 + (light - 0.52) * 1.0
    n["tumble"] += (sat - 0.38) * 2.6 + (light - 0.55) * 0.9
    n["counter"] += (sat - 0.40) * 2.0
    n["noir"] += (0.40 - sat) * 1.4 + (0.45 - light) * 2.0
    n["pulse"] += (sat - 0.42) * 1.8 + (0.48 - light) * 1.6
    n["binder"] += (0.44 - sat) * 1.8 + (light - 0.50) * 0.8
    # A premium or luxury band suits the quieter, editorial Pack; a budget one
    # suits the loud and the friendly. These were authored at roughly three
    # times the weight of the colour terms above, which made the band the whole
    # decision rather than one signal in it: every muted premium product - a
    # candle, a notebook, a bottle - went to noir on the +1.4 alone. Scaled
    # into the same range as colour so the three signals actually compete.
    BAND_WEIGHT = 0.45
    band_shift = {}
    if band in ("premium", "luxury"):
        band_shift = {"noir": 1.4, "binder": 0.5, "counter": -0.8}
    elif band == "budget":
        band_shift = {"counter": 0.9, "bloom": 0.3, "tumble": 0.6,
                      "noir": -0.9, "pulse": 0.4}
    for slug, shift in band_shift.items():
        if slug in n:
            n[slug] += shift * BAND_WEIGHT
    # Wide objects sit well in the full-bleed athletic hero.
    if orient == "wide":
        n["counter"] += 0.3
    elif orient == "tall":
        n["noir"] += 0.3
    return {k: round(v, 2) for k, v in n.items()}


def score_packs(spec: dict, explain: bool = False):
    """
    Score every Pack against one Product Spec.

    Exposed (and logged per job) so the reason a Pack won is inspectable rather
    than magic. With explain=True, returns the per-Pack breakdown instead.
    """
    category = (spec.get("category") or UNCATEGORISED).lower()
    text = _haystack(spec)
    nudge = _tiebreak(spec)

    hits = {slug: _hits(text, pack["keywords"]) for slug, pack in PACKS.items()}
    miss = {slug: _hits(text, pack["against"]) for slug, pack in PACKS.items()}

    # The tiebreak reads the photo to separate products nothing else
    # recognised. Applied at full strength it also overturns the signals that
    # DID fire: a cotton kurta carries category=apparel, which pulse wins 2.4
    # to noir's 2.0, and a 0.36 colour nudge handed it to noir anyway. So it is
    # damped whenever the Spec actually said something, and left at full
    # strength only for the case it was written for.
    recognised = category != UNCATEGORISED or any(hits.values())
    damp = 0.3 if recognised else 1.0

    scores, detail = {}, {}
    for slug, pack in PACKS.items():
        # "other" is not a category, it is the absence of one. Giving every
        # Pack a weight for it turned the fallback into the decision: noir
        # carried the highest "other" weight and so won products no keyword had
        # recognised - a linen cushion, letterpress cards, a scented candle -
        # by a fixed margin the photo-derived tiebreak could not close. With no
        # category, no Pack gets a category bonus, and the keywords and the
        # photo decide. That is what they are for.
        base = (0.0 if category == UNCATEGORISED
                else pack["categories"].get(category, 0.5))
        pos, neg = hits[slug], miss[slug]
        tie = round(nudge[slug] * damp, 2)
        total = base + sum(w for _k, w in pos) - sum(w for _k, w in neg) + tie
        scores[slug] = round(total, 2)
        detail[slug] = {"category": base, "matched": pos, "against": neg,
                        "tiebreak": tie, "total": round(total, 2)}
    return detail if explain else scores


class NoPacksInstalled(RuntimeError):
    """Raised when a Site is requested but no design has been added yet."""


def select_pack(spec: dict, spin: int = 0) -> str:
    """
    Choose the Pack for this Product Spec.

    Spin walks down the ranking rather than re-rolling randomly, so the second
    design a seller sees is the runner-up rather than a coin toss — and the
    same Spec always produces the same first choice (§8 determinism).
    """
    if not PACKS:
        raise NoPacksInstalled(
            "No designs installed. Add one under templates/packs/<slug>/ and "
            "register it in PACKS — see the contract at the top of core/packs.py.")
    scores = score_packs(spec)
    ranked = sorted(scores, key=lambda s: (-scores[s], s))
    if spin <= 0:
        return ranked[0]
    return ranked[spin % len(ranked)]


def get_pack(slug: str) -> dict:
    if not PACKS:
        raise NoPacksInstalled("No designs installed.")
    chosen = slug if slug in PACKS else (DEFAULT_PACK or next(iter(PACKS)))
    pack = dict(PACKS[chosen])
    pack["slug"] = chosen
    pack["asset_base"] = f"/packs/{pack['slug']}"
    return pack


def pack_layout(slug: str) -> list:
    return list(get_pack(slug)["sections"])


def packs_installed() -> bool:
    return bool(PACKS)
