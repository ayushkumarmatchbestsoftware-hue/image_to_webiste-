"""
Composition — decides what a page should MAKE A MOMENT OF.

The complaint that produced this module was exact: the output looked like a
template with the photos dropped into it. It did. Every section was the same
width with the same padding and the same left-aligned heading, all the way
down; change the words and the colours and the skeleton still showed through.

Three things are decided here, all from content that actually exists:

  rhythm    each section's treatment — contained, band, offset, inset — so the
            page has a beat instead of a uniform stack
  invert    one section flips to an ink ground, giving the page a centre of
            gravity
  feature   ONE element is set enormous: a real figure, a short quote, the
            price. Never more than one — a page where everything shouts is a
            page where nothing does. If nothing scores, nothing is emitted,
            because an arbitrary sentence at 13rem looks worse than no feature.

Everything the Packs already do survives it: `sect()` is additive, so a Pack's
own classes (binder's `band` IS its dark ledger strip) are kept verbatim and
the treatment is added alongside.
"""
import hashlib
import logging
import re

logger = logging.getLogger("composition")

# The treatments a section can take, and the orders they read well in. A page
# that goes contained → offset → band → inset has a beat; six contained
# sections in a row is a list. Several orders exist so two sellers who land on
# the same Pack still get differently-paced pages.
CYCLES = (
    ("contained", "offset", "band", "inset"),
    ("contained", "band", "offset", "contained"),
    ("offset", "contained", "inset", "band"),
    ("contained", "inset", "offset", "band"),
)

# What each treatment adds to a section's class list. "contained" adds nothing
# — it is the Pack's own default.
CLASS_FOR = {"contained": "", "band": "band",
             "offset": "t-offset", "inset": "t-inset"}


def _seed(spec: dict, salt: str = "") -> int:
    key = f"{spec.get('sub_type','')}|{spec.get('mood','')}|{spec.get('product_type','')}|{salt}"
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16)


def _words(s) -> int:
    return len(re.findall(r"\w+", str(s or "")))


def pick_feature(data: dict, price: str = "", seed: int = 0,
                 prefer: str = "") -> dict:
    """
    Choose the ONE thing worth making big, and say why.

    Ranked by how much the content supports the treatment: a figure with real
    digits beats a vague one, a short specific quote beats a long bland one, a
    two-word title can carry display size where a twelve-word one cannot.
    """
    # An explicit "none" from the Art Director is a decision: the page gets no
    # oversized element at all. That is a real answer, and overriding it with a
    # seeded pick would be ignoring the agent.
    if prefer == "none":
        return {}

    cands = []

    # A real figure is the strongest thing a small seller has. "Since 1998" set
    # 200px tall is a design element; the same fact in a sentence is not.
    for st in (data.get("stats") or [])[:4]:
        num = str(st.get("number", "")).strip()
        if not num or not any(c.isdigit() for c in num):
            continue
        score = 3.0 + sum(c.isdigit() for c in num) * 0.4 - max(0, len(num) - 6) * 0.35
        cands.append({"kind": "stat", "score": score, "big": num,
                      "label": st.get("label", ""),
                      "why": f"a real figure ({num}) carries a page better than a sentence"})

    # A short, concrete review reads as a quote; a long one reads as a wall.
    for t in (data.get("testimonials") or [])[:3]:
        txt = (t.get("text") or "").strip()
        n = _words(txt)
        if not (5 <= n <= 24):
            continue
        specific = bool(re.search(r"\d|month|year|wash|day|week|size", txt, re.I))
        score = 2.6 + (0.7 if specific else 0) - abs(n - 14) * 0.05
        cands.append({"kind": "quote", "score": score, "big": txt,
                      "label": t.get("name", ""),
                      "why": "a short, specific quote sets well at size"})

    # The price is what a buyer is scanning for.
    if str(price or "").strip():
        cands.append({"kind": "price", "score": 2.3, "big": str(price).strip(),
                      "label": (data.get("home", {}) or {}).get("cta", "Enquire"),
                      "why": "the price is the thing a buyer is looking for"})

    # A very short title wants to be set enormous. A long one does not.
    title = (data.get("site_info", {}) or {}).get("site_title", "")
    if 2 <= _words(title) <= 5:
        cands.append({"kind": "headline", "score": 2.5, "big": title,
                      "label": (data.get("home", {}) or {}).get("label", ""),
                      "why": f"a {_words(title)}-word title can carry display size"})

    if not cands:
        return {}
    # Scoring alone hands every seller the same treatment. Copy generation
    # almost always produces a stat, and a four-digit figure outscores the best
    # quote by more than a point, so every site in the world would open on a
    # giant number.
    #
    # So the choice is made between KINDS, not between raw candidates: keep the
    # strongest of each kind, then let the seed pick among the top three. Every
    # candidate that gets this far has already passed its own quality gate — a
    # stat needs real digits, a quote has to be short and specific, a headline
    # has to be five words or fewer — so any of the three is a defensible page.
    # What is gained is that two sellers get different moments.
    best_of = {}
    for c in cands:
        if c["kind"] not in best_of or c["score"] > best_of[c["kind"]]["score"]:
            best_of[c["kind"]] = c
    ranked = sorted(best_of.values(), key=lambda c: -c["score"])
    # The Art Director may ask for a particular kind. It is honoured only when
    # a candidate of that kind actually cleared its quality gate — the agent
    # chooses before the copy exists, so it is expressing a preference about
    # this product, not an assertion that the content is there.
    if prefer and prefer in best_of:
        best = dict(best_of[prefer])
        best["score"] = round(best["score"], 2)
        best["from"] = len(ranked)
        best["why"] = "art director asked for a " + prefer + "; " + best["why"]
        return best
    pool = ranked[:3]
    best = dict(pool[seed % len(pool)])
    best["score"] = round(best["score"], 2)
    best["from"] = len(pool)
    return best


def plan_composition(data: dict, spec: dict, price: str = "",
                     spin: int = 0, n_sections: int = 6,
                     rhythm: list = None, invert_at: int = -1,
                     prefer_feature: str = "") -> dict:
    """
    Plan the page: a treatment per section index, one inverted section, and at
    most one feature. Plain data, so the templates stay declarative.
    """
    # An Art Director's plan overrides the seeded one. It arrives already
    # validated against TREATMENTS, so it is used as given.
    if rhythm:
        seq = list(rhythm)[:n_sections]
        while len(seq) < n_sections:
            seq.append("contained")
        feature = pick_feature(data, price,
                               seed=_seed(spec, "feature") + spin,
                               prefer=prefer_feature)
        if feature:
            logger.info(f"feature: {feature['kind']} — {feature['why']}")
        at = invert_at if invert_at != -1 else None
        if isinstance(at, int) and not (1 <= at <= max(1, n_sections - 2)):
            at = None
        return {"seq": seq, "invert_at": at, "feature": feature}

    cycle = CYCLES[(_seed(spec, "cycle") + spin) % len(CYCLES)]
    off = _seed(spec, "phase") % len(cycle)
    # The section straight after the hero stays calm — an offset rail or an
    # inset panel immediately under a hero fights it.
    seq = ["contained"] + [cycle[(off + i) % len(cycle)]
                           for i in range(max(0, n_sections - 1))]

    # One section on an ink ground. Never the first (it would fight the hero)
    # and never the last (it would fight the footer).
    invert_at = None
    if n_sections >= 3:
        invert_at = 1 + (_seed(spec, "invert") + spin) % max(1, n_sections - 2)

    feature = pick_feature(data, price, seed=_seed(spec, "feature") + spin,
                           prefer=prefer_feature)
    if feature:
        logger.info(f"feature: {feature['kind']} of {feature.get('from')} "
                    f"candidates — {feature['why']}")

    return {"seq": seq, "invert_at": invert_at, "feature": feature}


def make_sect(comp: dict):
    """
    Build the `sect(i, base='')` the templates call in place of a class list.

    Additive: the Pack's own classes come through untouched, the planned
    treatment is added, and `reveal` opts the section into the arrival motion.
    Returns a full `class="…"` attribute so a section with no classes of its
    own does not end up with a stray empty attribute.
    """
    seq = (comp or {}).get("seq") or []
    invert_at = (comp or {}).get("invert_at")

    def sect(i: int, base: str = "") -> str:
        parts = [p for p in str(base or "").split() if p]
        treat = CLASS_FOR.get(seq[i] if i < len(seq) else "contained", "")
        if treat and treat not in parts:
            parts.append(treat)
        if invert_at is not None and i == invert_at:
            parts.append("invert")
        parts.append("reveal")
        from markupsafe import Markup
        return Markup(f'class="{" ".join(parts)}"')

    return sect
