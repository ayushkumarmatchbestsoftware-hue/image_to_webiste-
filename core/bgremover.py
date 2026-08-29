"""
Background replacement by image model — the skills/bg-remover.md path.

The local cut-out in imagedirector.py segments: it decides which pixels are
product and drops the rest. That works until the product has thin structures.
A chair on a five-star base defeats it twice over — the legs come out ragged,
and a pale ellipse of floor gets confidently INCLUDED as though it were part
of the chair. No amount of post-processing recovers from a mask that is wrong
about what the subject is.

This asks an image model to re-render the photograph on a different background
instead. It does not segment, so thin structures survive and there is no mask
to be wrong.

Three things keep it honest:

  The INPUT is always the seller's own uploaded photo. The model edits their
  photograph; it never invents a product.

  The background is decided by the SITE, not by a prompt someone wrote. It is
  the Grade's own ground colour, so the product sits on the page's surface
  rather than on a cut-out island in a colour that appears nowhere else.

  The OUTPUT is checked before it is accepted. The model returns a photograph,
  and a photograph can quietly be of a different product — restyled, recoloured,
  reproportioned. A buyer receives the real thing, so a result that has drifted
  is discarded rather than shipped.

Provider-agnostic, the same way core/llm.py is: the image APIs differ in shape,
so each has a small adapter, and the provider is chosen from whichever key is
present. Swapping GEMINI_API_KEY for OPENAI_API_KEY changes nothing else.

Never load-bearing: no key, a 429, a failed check, and the caller falls back to
the local cut-out.
"""
import base64
import json
import logging
import os
import urllib.error
import urllib.request
import uuid

logger = logging.getLogger("bgremover")

ENABLED = os.getenv("BG_REPLACE", "1") not in ("0", "false", "False")
TIMEOUT = float(os.getenv("BG_REPLACE_TIMEOUT", "60"))

# Which provider to use is NOT decided here. core/llm.py already resolves it
# from LLM_PROVIDER or whichever key is set, and re-deciding it in a second
# place is how the two end up disagreeing — imagery calling one vendor while
# the copy is written by another. Imagery follows the text provider unless
# IMAGE_PROVIDER explicitly says otherwise.
#
# Everything below is a DEFAULT, overridable by environment, because model
# names and routes change faster than this file will:
#
#   IMAGE_PROVIDER        use a different vendor for imagery than for text
#   BG_REPLACE_MODELS     comma-separated model names, in preference order
#   BG_REPLACE_ENDPOINT   the full URL template, if a route moves
#
# The API SHAPES genuinely differ — Gemini takes the photo inline in a JSON
# body, OpenAI takes a multipart upload on its own images/edits route — so each
# vendor gets an adapter. That is not configuration; it is the protocol.
DEFAULT_MODELS = {
    "gemini": "gemini-3.1-flash-image,gemini-2.5-flash-image",
    "openai": "gpt-image-1",
}
DEFAULT_ENDPOINT = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
    "openai": "{base}/images/edits",
}
ADAPTER = {"gemini": "_call_gemini", "openai": "_call_openai"}


# How far the product may drift before the result is rejected. Generous enough
# to allow the lighting change that comes with a new background, tight enough
# to catch a model that has redrawn the thing.
MAX_COLOUR_DRIFT = 42      # mean RGB distance across the product region
MAX_ASPECT_DRIFT = 0.06    # proportion


class Unavailable(Exception):
    """No key, no quota, or no model. The caller should fall back quietly."""


def provider() -> str:
    """
    Which vendor edits the image.

    IMAGE_PROVIDER wins if it names one this module has an adapter for.
    Otherwise it is whatever core/llm.py already resolved for text — so
    swapping the key swaps everything together, which is the whole point.
    """
    want = (os.getenv("IMAGE_PROVIDER") or "").strip().lower()
    if want in ADAPTER:
        return want
    try:
        from core.llm import PROVIDER, PROVIDERS
        if PROVIDER in ADAPTER and os.getenv(PROVIDERS[PROVIDER]["key_env"]):
            return PROVIDER
    except Exception:
        pass
    return ""


def _key(name: str) -> str:
    """The API key, read from whatever core/llm.py says this vendor calls it."""
    from core.llm import PROVIDERS
    return os.getenv(PROVIDERS.get(name, {}).get("key_env", ""), "")


def _endpoint(name: str, model: str) -> str:
    from core.llm import PROVIDERS
    tmpl = os.getenv("BG_REPLACE_ENDPOINT") or DEFAULT_ENDPOINT.get(name, "")
    # base_url is None for whichever vendor the SDK treats as its default, so
    # that one case needs a value. Overridable rather than fixed.
    base = (PROVIDERS.get(name, {}).get("base_url")
            or os.getenv("OPENAI_BASE_URL")
            or "https://api.openai.com/v1").rstrip("/")
    return tmpl.format(model=model, base=base)


def models_for(name: str) -> list:
    spec = os.getenv("BG_REPLACE_MODELS") or DEFAULT_MODELS.get(name, "")
    return [m.strip() for m in spec.split(",") if m.strip()]


def available() -> bool:
    name = provider()
    return bool(ENABLED and name and models_for(name))


def info() -> dict:
    """For /health, so the UI can say why this is or is not running."""
    name = provider()
    return {"enabled": ENABLED, "provider": name or None,
            "models": models_for(name) if name else [],
            "usable": available()}


def build_prompt(theme: dict) -> str:
    """
    The prompt, built from the site rather than from a request.

    Thin structures get their own sentence because they are the entire reason
    this path exists — they are what the local cut-out loses.
    """
    ground = (theme or {}).get("bg_alt") or (theme or {}).get("bg") or "#f4f2ef"
    return (
        f"Replace the background of this product photograph with a flat, even "
        f"{ground} surface.\n"
        "Keep the product exactly as it is - same pose, framing, proportions, "
        "colour and every detail. Do not restyle, redraw or beautify the "
        "product itself.\n"
        "Cut cleanly around the product with no halo, glow or colour fringing.\n"
        "Preserve every thin structure exactly - legs, bases, handles, chains, "
        "spokes, straps - do not thicken, smooth or drop them.\n"
        "Remove any shadow, reflection or floor from the original photograph."
    )


# ── Provider adapters ────────────────────────────────────────────────────────

def _call_gemini(model: str, prompt: str, photo: bytes) -> bytes:
    """Native generateContent: the photo goes in inline, an image comes back inline."""
    key = _key("gemini")
    body = {"contents": [{"parts": [
        {"text": prompt},
        {"inline_data": {"mime_type": "image/jpeg",
                         "data": base64.b64encode(photo).decode()}},
    ]}]}
    req = urllib.request.Request(
        _endpoint("gemini", model) + f"?key={key}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = json.load(r)
    for part in data.get("candidates", [{}])[0].get("content", {}).get("parts", []):
        blob = part.get("inline_data") or part.get("inlineData")
        if blob and blob.get("data"):
            return base64.b64decode(blob["data"])
    raise Unavailable(f"{model} returned no image")


def _call_openai(model: str, prompt: str, photo: bytes) -> bytes:
    """
    images/edits: a multipart upload on a different route entirely.

    Built by hand rather than with the SDK so this module stays dependency-free
    and the two adapters read the same way.
    """
    key = _key("openai")
    boundary = "----bg" + uuid.uuid4().hex

    def part(name, value):
        return (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
                f'\r\n\r\n{value}\r\n').encode()

    body = part("model", model) + part("prompt", prompt)
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
             f'filename="product.jpg"\r\nContent-Type: image/jpeg\r\n\r\n').encode()
    body += photo + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        _endpoint("openai", model), data=body,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = json.load(r)
    for item in data.get("data", []):
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        if item.get("url"):
            with urllib.request.urlopen(item["url"], timeout=TIMEOUT) as im:
                return im.read()
    raise Unavailable(f"{model} returned no image")


def _accepts(before: bytes, after: bytes, strict: bool = False) -> bool:
    """
    Is this still a photograph of the same product?

    The model can return something beautiful that is not what the seller sells.
    Two cheap checks catch the common failures: a redrawn product shifts colour
    a long way, and a reframed one changes proportion.
    """
    try:
        import io
        from PIL import Image, ImageStat
    except Exception:
        return True                      # cannot check; trust the caller's gate

    try:
        a = Image.open(io.BytesIO(before)).convert("RGB")
        b = Image.open(io.BytesIO(after)).convert("RGB")
    except Exception:
        return False

    # Staging replaces the whole frame, so the product has more room to drift
    # and the tolerance tightens rather than loosens.
    colour_limit = MAX_COLOUR_DRIFT * (0.7 if strict else 1.0)
    ar, br = a.size[0] / a.size[1], b.size[0] / b.size[1]
    if abs(ar - br) / max(ar, br) > MAX_ASPECT_DRIFT:
        logger.warning(f"rejected: aspect moved {ar:.2f} -> {br:.2f}")
        return False

    # Compare the middle of the frame, where the product is, rather than the
    # whole image — the background is supposed to have changed.
    def core(im):
        w, h = im.size
        return im.crop((int(w * .25), int(h * .25), int(w * .75), int(h * .75)))
    ma = ImageStat.Stat(core(a)).mean
    mb = ImageStat.Stat(core(b.resize(a.size))).mean
    drift = sum((x - y) ** 2 for x, y in zip(ma, mb)) ** 0.5
    if drift > colour_limit:
        logger.warning(f"rejected: product colour drifted {drift:.0f}")
        return False
    return True


def replace(photo: bytes, theme: dict) -> bytes:
    """
    Return the seller's photo with its background replaced, or raise Unavailable.

    Raising rather than returning None keeps the caller's fallback explicit —
    this must never be the reason a generation fails.
    """
    name = provider()
    if not (ENABLED and name):
        raise Unavailable("no image provider configured")

    call = globals()[ADAPTER[name]]
    prompt = build_prompt(theme)
    last = None

    for model in models_for(name):
        try:
            out = call(model, prompt, photo)
        except urllib.error.HTTPError as e:
            last = f"{model}: HTTP {e.code}"
            if e.code == 429:
                # Image models are metered separately from text models. A key
                # that writes copy all day can still be at zero for these, and
                # reading that as "the feature is broken" wastes a lot of time.
                logger.info(f"{name}/{model}: out of image quota (429)")
            else:
                logger.warning(f"{name}/{model}: HTTP {e.code}")
            continue
        except Exception as e:
            last = f"{model}: {type(e).__name__}"
            logger.warning(f"{name}/{model} failed: {e}")
            continue

        if not _accepts(photo, out):
            last = f"{model}: result did not match the original product"
            continue
        logger.info(f"background replaced by {name}/{model} "
                    f"({len(out)//1024}KB, ground {theme.get('bg_alt')})")
        return out

    raise Unavailable(last or f"no {name} image model succeeded")


# ── Staging: putting the product somewhere real ──────────────────────────────
#
# A cut-out on a flat colour says "this was photographed against something we
# removed". The same product on a bench at a cricket ground says "this is what
# owning it looks like". Only one of those sells.
#
# It also solves a problem no processing can. A seller's photo holds a product
# a few hundred pixels wide; nothing cut from it fills a full-bleed hero
# without going soft. A staged photograph is generated at its own resolution,
# which is what makes that hero possible at all.
#
# Criteria live in skills/product-staging.md.

STAGE = os.getenv("STAGE_PRODUCT", "1") not in ("0", "false", "False")

_SCENE_SYSTEM = """You place products into the setting they belong in.

Given a product description, name the real place that product lives - the
surface it rests on, what is behind it out of focus, and the light.

Rules:
- One sentence. A long description makes an image model invent clutter that
  competes with the product.
- Name a place a buyer would recognise as where this thing actually is. A
  marble worktop under studio lighting flatters everything and belongs to
  nothing, which is why it reads as stock photography.
- Describe only the SETTING. Never describe the product itself.

Return JSON: {"scene": "<one sentence>"}"""


def describe_scene(spec: dict) -> str:
    """
    Ask the text model where this product belongs.

    Deliberately the TEXT model, which works on keys whose image quota is
    exhausted — so the scene is ready and product-specific long before the
    image call can run. A fixed list of settings would be the stock-photo
    problem in another form.
    """
    bits = {k: spec.get(k) for k in
            ("sub_type", "category", "material", "finish", "mood") if spec.get(k)}
    if not bits:
        return ""
    try:
        import asyncio
        from core.llm import chat_json, MODEL_FAST
        out = asyncio.run(chat_json(
            system=_SCENE_SYSTEM, text=json.dumps(bits, ensure_ascii=False),
            model=MODEL_FAST, temperature=0.6, max_tokens=200))
        scene = ((out or {}).get("scene") or "").strip()
        if scene:
            logger.info(f"scene: {scene}")
            return scene[:240]
    except Exception as e:
        logger.info(f"scene description unavailable ({e}); staging skipped")
    return ""


def stage_prompt(scene: str) -> str:
    """
    The staging prompt.

    The product is described as untouchable twice, in different words, because
    this is the one thing that must not drift: a buyer receives the real object,
    and a restyled photograph is a lie the seller never told.
    """
    return (
        f"Place this exact product into {scene}\n"
        "Keep the product itself completely unchanged - same shape, colour, "
        "proportions, markings and wear. Do not redraw, restyle or beautify it.\n"
        "Photograph it as a real product photograph with shallow depth of field "
        "and the background softly out of focus.\n"
        "The product must remain the clear subject and must not be cropped."
    )


def stage(photo: bytes, spec: dict) -> bytes:
    """
    Return the seller's product photographed in a setting of its own world.

    Raises Unavailable so the caller falls back — to a flat plate, then the
    local cut-out, then the untouched photo. Each is worse looking and none is
    wrong, which is the right way round.
    """
    name = provider()
    if not (ENABLED and STAGE and name):
        raise Unavailable("staging disabled or no image provider")
    scene = describe_scene(spec)
    if not scene:
        raise Unavailable("no scene could be described for this product")

    call = globals()[ADAPTER[name]]
    prompt = stage_prompt(scene)
    last = None
    for model in models_for(name):
        try:
            out = call(model, prompt, photo)
        except urllib.error.HTTPError as e:
            last = f"{model}: HTTP {e.code}"
            logger.info(f"{name}/{model}: "
                        + ("out of image quota (429)" if e.code == 429
                           else f"HTTP {e.code}"))
            continue
        except Exception as e:
            last = f"{model}: {type(e).__name__}"
            continue
        # Stricter than a background swap: far more of the frame has changed,
        # so there is far more room for the product to have changed with it.
        if not _accepts(photo, out, strict=True):
            last = f"{model}: staged product no longer matches the original"
            continue
        logger.info(f"staged by {name}/{model} ({len(out)//1024}KB)")
        return out
    raise Unavailable(last or f"no {name} image model succeeded")
