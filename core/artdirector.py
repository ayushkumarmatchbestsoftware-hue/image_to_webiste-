"""
The Art Director - the one agent in the pipeline.

Everything else here is a rule: the Grade is colour maths, the pack score is
keyword weights, the treatment plan is thresholds. Rules are fast, free and
debuggable, and they are the right tool wherever the question has a measurable
answer.

They cannot answer "does this look wrong". That gap is real and it has cost:
this build shipped white text on a white ground and a hero cropped through the
product, and both were caught by looking at the rendered page, not by any rule.

So this module does two things a rule cannot:

  direct()    before rendering, reads the Spec and the seller's own words and
              chooses the design, the opening, the feature and the pacing —
              judgement about meaning, where the keyword score only sees words

  critique()  after rendering, LOOKS at a screenshot of the finished page and
              names what is visibly wrong with it, then the page is repaired
              and rebuilt once

Both are driven by skills/*.md, so the criteria are editable text rather than a
prompt buried in here.

Two design rules hold this together:

  1. The agent chooses from a fixed vocabulary and never writes markup. Every
     value it returns is validated against the allowed set, and anything
     unrecognised falls back to the deterministic choice.
  2. Every stage is optional. No key, no Chrome, a refused call, a malformed
     reply - each falls back to the rules and the site still ships. The agent
     improves the page; it is never load-bearing.
"""
import asyncio
import glob
import json
import logging
import os
import shutil
import subprocess
import tempfile

from core import skills

logger = logging.getLogger("artdirector")

# The vocabulary. The agent may only return values from these sets; anything
# else is a hallucinated option and is discarded rather than passed downstream.
HERO_VARIANTS = ("side-right", "side-left", "bleed", "plate", "below", "inset")
FEATURE_KINDS = ("stat", "quote", "price", "headline", "none")
TREATMENTS = ("contained", "band", "offset", "inset")
FIXES = ("hero_variant", "feature_kind", "invert_off", "invert_at",
         "reshuffle", "image_contain", "report_only")

# How long the whole agent may take before the page ships as it is. The budget
# is generous because the user set it that way, but it is still a budget: a
# hung provider must not hold a generation open.
DIRECT_TIMEOUT = float(os.getenv("AD_DIRECT_TIMEOUT", "20"))
CRITIQUE_TIMEOUT = float(os.getenv("AD_CRITIQUE_TIMEOUT", "25"))
SHOT_TIMEOUT = float(os.getenv("AD_SHOT_TIMEOUT", "30"))

ENABLED = os.getenv("ART_DIRECTOR", "1") not in ("0", "false", "False")


# ── Seeing the page ───────────────────────────────────────────────────────────

_CHROME_HINTS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def find_chrome() -> str:
    """
    Locate a Chromium-family browser, or "" if there is none.

    Deliberately not a new dependency: a headless Chrome is already on every
    machine this runs on, and shelling out to it costs nothing to install. If it
    is genuinely absent the critique is skipped and the page ships unreviewed.
    """
    for name in ("chrome", "chromium", "google-chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    for path in _CHROME_HINTS:
        if os.path.exists(path):
            return path
    return ""


def shoot(html: str, width: int = 1440, height: int = 2600) -> bytes:
    """
    Render `html` (already self-contained) and return a PNG, or b"" on failure.

    Reduced motion is forced. The page's arrival animation starts sections at
    zero opacity, and a screenshot taken mid-flight shows a washed-out page that
    would be critiqued as a contrast defect that no user will ever see.
    """
    chrome = find_chrome()
    if not chrome:
        logger.info("no Chrome found - page will ship without critique")
        return b""

    tmp = tempfile.mkdtemp(prefix="ad_")
    page, shot = os.path.join(tmp, "page.html"), os.path.join(tmp, "shot.png")
    try:
        with open(page, "w", encoding="utf-8") as fh:
            fh.write(html)
        subprocess.run(
            [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
             "--force-prefers-reduced-motion", "--no-sandbox",
             f"--window-size={width},{height}",
             "--virtual-time-budget=5000",
             f"--screenshot={shot}", f"file:///{page.replace(os.sep, '/')}"],
            timeout=SHOT_TIMEOUT, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=False)
        if os.path.exists(shot):
            with open(shot, "rb") as fh:
                return fh.read()
        logger.warning("screenshot produced no file")
    except subprocess.TimeoutExpired:
        logger.warning(f"screenshot timed out after {SHOT_TIMEOUT}s")
    except Exception as e:
        logger.warning(f"screenshot failed: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return b""


# ── Before the render: choosing the page ──────────────────────────────────────

async def direct(spec: dict, seller_facts: str, packs: dict, n_sections: int,
                 feature_options: list, fallback: dict,
                 staged: bool = False) -> dict:
    """
    Choose pack, hero, feature, rhythm and invert. Returns `fallback` unchanged
    if the agent is unavailable or answers with anything unusable.

    `fallback` is what the deterministic rules already decided, so a failure
    here costs the page nothing - it simply does not get the upgrade.
    """
    skill = skills.load("art-direction")
    if not (ENABLED and skill):
        return dict(fallback, _agent="off")

    from core.llm import chat_json, MODEL_FAST, api_dead
    # api_dead() returns (bool, reason) - testing the tuple itself is always
    # truthy, which silently disabled the agent on every run.
    dead, why = api_dead()
    if dead:
        logger.info(f"direction skipped - model API unavailable ({why})")
        return dict(fallback, _agent="api-dead")

    menu = {
        slug: {"character": p.get("character", ""),
               "use_case": p.get("use_case", ""),
               "mode": p.get("mode", "light"),
               "sections": p.get("sections", [])}
        for slug, p in packs.items()
    }
    payload = {
        "product_spec": spec,
        # Whether the photograph is a staged scene decides whether a full-bleed
        # opening is even possible, so the director is told rather than left to
        # guess from the Spec.
        "photo_is_staged": bool(staged),
        "seller_said": seller_facts or "(nothing)",
        "designs_available": menu,
        "feature_options": feature_options,
        "sections_on_this_page": n_sections,
        "rules_would_have_chosen": {k: v for k, v in fallback.items()
                                    if not k.startswith("_")},
    }
    schema = (
        '{"pack":"<slug>","pack_reason":"<one line>",'
        '"hero":"<variant>","hero_reason":"<one line>",'
        '"feature":"<kind>","feature_reason":"<one line>",'
        f'"rhythm":[{n_sections} treatments],'
        '"invert_at":<index or null>}')
    try:
        out = await asyncio.wait_for(
            chat_json(system=skill,
                      text=json.dumps(payload, ensure_ascii=False)
                           + f"\n\nReturn ONLY this JSON object:\n{schema}",
                      model=MODEL_FAST, temperature=0.4, max_tokens=1400),
            timeout=DIRECT_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning(f"direction timed out after {DIRECT_TIMEOUT}s")
        return dict(fallback, _agent="timeout")
    except Exception as e:
        logger.warning(f"direction failed: {e}")
        return dict(fallback, _agent="error")
    if not out:
        return dict(fallback, _agent="empty")

    # Validate every field. An agent naming a pack that does not exist, or a
    # hero variant it invented, must not reach the renderer.
    chosen = dict(fallback)
    chosen["_agent"] = "on"
    chosen["_reasons"] = {}

    if out.get("pack") in packs:
        chosen["pack"] = out["pack"]
        chosen["_reasons"]["pack"] = str(out.get("pack_reason", ""))[:160]
    elif out.get("pack"):
        logger.warning(f"agent named unknown pack {out['pack']!r} - kept {fallback['pack']!r}")

    if out.get("hero") in HERO_VARIANTS:
        chosen["hero"] = out["hero"]
        chosen["_reasons"]["hero"] = str(out.get("hero_reason", ""))[:160]

    if out.get("feature") in FEATURE_KINDS:
        # "none" is kept as a string. It is a decision, not an absence, and
        # collapsing it to None made it indistinguishable from "did not answer".
        chosen["feature_kind"] = out["feature"]
        chosen["_reasons"]["feature"] = str(out.get("feature_reason", ""))[:160]

    rhythm = out.get("rhythm")
    if isinstance(rhythm, list) and rhythm:
        clean = [t for t in rhythm if t in TREATMENTS][:n_sections]
        if len(clean) == len(rhythm[:n_sections]) and clean:
            # The section under the hero stays calm whatever the agent says.
            clean[0] = "contained"
            while len(clean) < n_sections:
                clean.append("contained")
            chosen["rhythm"] = clean

    # Only when the key is actually present. An agent that omits it is saying
    # nothing, not asking for no inverted section - and -1 (the fallback) means
    # "let the seeded planner decide", which is a third distinct answer.
    if "invert_at" in out:
        inv = out["invert_at"]
        if inv is None or (isinstance(inv, int)
                           and 1 <= inv <= max(1, n_sections - 2)):
            chosen["invert_at"] = inv

    for k, v in chosen["_reasons"].items():
        if v:
            logger.info(f"director {k}: {v}")
    return chosen


# ── After the render: looking at it ───────────────────────────────────────────

async def critique(png: bytes, chosen: dict) -> dict:
    """
    Look at the rendered page and name what is visibly wrong.

    Returns {"verdict": ..., "defects": [...]} with every defect validated
    against the allowed fix vocabulary, or an empty verdict when unavailable.
    """
    skill = skills.load("page-critique")
    if not (ENABLED and skill and png):
        return {"verdict": "ship", "defects": [], "_agent": "off"}

    from core.llm import chat_json, MODEL_FAST, api_dead, supports_vision
    dead, why = api_dead()
    if dead or not supports_vision():
        logger.info(f"critique skipped - {why or 'provider has no vision'}")
        return {"verdict": "ship", "defects": [], "_agent": "no-vision"}

    context = ("This page was built with: "
               f"design={chosen.get('pack')}, hero={chosen.get('hero')}, "
               f"feature={chosen.get('feature_kind') or 'none'}, "
               f"inverted section={chosen.get('invert_at')}.")
    try:
        out = await asyncio.wait_for(
            chat_json(system=skill,
                      text=context + "\n\nLook at the screenshot and return the JSON.",
                      images=[png], model=MODEL_FAST,
                      temperature=0.2, max_tokens=1200),
            timeout=CRITIQUE_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning(f"critique timed out after {CRITIQUE_TIMEOUT}s")
        return {"verdict": "ship", "defects": [], "_agent": "timeout"}
    except Exception as e:
        logger.warning(f"critique failed: {e}")
        return {"verdict": "ship", "defects": [], "_agent": "error"}
    if not out:
        return {"verdict": "ship", "defects": [], "_agent": "empty"}

    defects = []
    for d in (out.get("defects") or [])[:4]:
        if not isinstance(d, dict):
            continue
        fix = d.get("fix")
        if fix not in FIXES:
            # An invented fix is downgraded to a note rather than dropped: the
            # observation may still be worth logging even when the remedy is not
            # one this system can carry out.
            d["fix"] = "report_only"
        defects.append({"severity": d.get("severity", "minor"),
                        "what": str(d.get("what", ""))[:200],
                        "fix": d["fix"], "value": d.get("value")})
    # Two separate questions, which the first version of this conflated.
    #
    #   verdict     is the page actually good? A severe defect means no, even
    #               when nothing here can fix it — reporting "ship" alongside a
    #               severe finding is how a known-bad page gets waved through.
    #   actionable  is there anything to DO about it? Re-rendering for a defect
    #               with no available fix just burns time and changes nothing.
    worst = ("severe" if any(d["severity"] == "severe" for d in defects)
             else "moderate" if any(d["severity"] == "moderate" for d in defects)
             else "minor" if defects else "clean")
    actionable = [d for d in defects
                  if d["fix"] != "report_only"
                  and d["severity"] in ("severe", "moderate")]
    verdict = "repair" if worst in ("severe", "moderate") else "ship"
    logger.info(f"critique: {worst} - {len(defects)} defect(s), "
                f"{len(actionable)} actionable")
    for d in defects:
        logger.info(f"  [{d['severity']}] {d['what']} -> {d['fix']}")
    return {"verdict": verdict, "defects": defects, "worst": worst,
            "actionable": actionable, "_agent": "on"}


def apply_repairs(defects: list, comp: dict, hero: str, plate_fit: bool):
    """
    Turn critique defects into concrete changes.

    Bounded by design: the agent picks from a fixed vocabulary and this maps
    each entry onto one parameter. It cannot inject markup or styles, so a bad
    critique produces a differently-composed page, never a broken one.

    Returns (comp, hero, plate_fit, applied) - `applied` is the audit trail.
    """
    comp = dict(comp)
    applied = []
    for d in defects:
        fix, val = d.get("fix"), d.get("value")
        if fix == "hero_variant" and val in HERO_VARIANTS and val != hero:
            hero, _ = val, applied.append(f"hero {hero} -> {val}")
        elif fix == "feature_kind" and val in FEATURE_KINDS:
            if val == "none":
                comp["feature"] = {}
                applied.append("feature removed")
            else:
                comp["_force_feature"] = val
                applied.append(f"feature -> {val}")
        elif fix == "invert_off":
            comp["invert_at"] = None
            applied.append("invert removed")
        elif fix == "invert_at" and isinstance(val, int):
            n = len(comp.get("seq") or [])
            if 1 <= val <= max(1, n - 2):
                comp["invert_at"] = val
                applied.append(f"invert -> section {val}")
        elif fix == "reshuffle":
            seq = comp.get("seq") or []
            if seq:
                comp["seq"] = [seq[0]] + seq[:0:-1]
                applied.append("rhythm re-paced")
        elif fix == "image_contain":
            plate_fit = True
            applied.append("band image letterboxed instead of cropped")
    return comp, hero, plate_fit, applied
