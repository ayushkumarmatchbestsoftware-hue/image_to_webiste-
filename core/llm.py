"""
OpenAI provider — replaces the Gemini client across the pipeline.

Everything that used to call `genai_client.models.generate_content(...)` now
routes through `chat_json()` / `chat_text()` here. Two behavioural differences
from the Gemini path this replaces:

  1. Images. Gemini accepted PIL objects directly; the OpenAI chat API wants
     base64 data URLs. `image_part()` does that conversion, and downscales on
     the way through — a 12MP phone photo is ~8MB of base64 that buys no extra
     accuracy over a 1536px longest edge, and costs latency on every call.

  2. JSON. The Gemini path asked for JSON in the prompt and then regex-stripped
     ``` fences off the reply, which failed silently whenever the model wrapped
     its output differently. OpenAI's response_format={"type":"json_object"}
     guarantees parseable JSON, so `chat_json()` has no fence-stripping and a
     parse failure is a real error rather than a shrug.

The client is constructed lazily and cached, mirroring how get_genai_client()
worked — most requests in this process never touch the model at all.
"""
import asyncio
import base64
import io
import json
import logging
import os
from typing import Optional

logger = logging.getLogger("llm")

# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
# Every one of these speaks the OpenAI chat-completions wire format, so the same
# client works against all of them — only base_url, key and model names change.
#
# vision=False means that provider CANNOT run detection (core/vision.py), which
# is the stage that reads the Source Photo. Those providers can still write copy;
# the pipeline falls back to the offline Spec for detection.
PROVIDERS = {
    "openai": {
        "base_url": None,                      # SDK default
        "key_env": "OPENAI_API_KEY",
        "content": "gpt-4.1", "fast": "gpt-4.1-mini",
        "vision": True, "free_tier": False,
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_env": "GEMINI_API_KEY",
        # Measured against this key: gemini-2.5-* is closed to new keys (404),
        # and gemini-flash-latest times out past 30s. These two answer quickly
        # and return clean JSON — flash-lite at ~0.9s for the short detection
        # call, 3.6-flash for the longer copy pass.
        # Measured on this key, writing a full site's copy:
        #   gemini-3.6-flash      @6000 tok = 23.7s   @3000 = 14.2s
        #   gemini-3.1-flash-lite @3000 tok =  3.4s
        # The PRD's latency target is p50 <= 45s for the WHOLE pipeline, and
        # a single 24s call spends half of it. flash-lite returns complete,
        # valid copy 7x faster; raise it with LLM_MODEL_CONTENT if richer
        # output is worth the seconds.
        "content": "gemini-3.1-flash-lite", "fast": "gemini-3.1-flash-lite",
        "vision": True, "free_tier": True,
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
        "content": "meta-llama/llama-4-maverick-17b-128e-instruct",
        "fast": "meta-llama/llama-4-scout-17b-16e-instruct",
        "vision": True, "free_tier": True,
    },
    "xai": {
        "base_url": "https://api.x.ai/v1",
        "key_env": "XAI_API_KEY",
        "content": "grok-2-vision-1212", "fast": "grok-2-vision-1212",
        "vision": True, "free_tier": False,
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
        "content": "google/gemini-2.0-flash-exp:free",
        "fast": "google/gemini-2.0-flash-exp:free",
        "vision": True, "free_tier": True,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "key_env": "DEEPSEEK_API_KEY",
        "content": "deepseek-chat", "fast": "deepseek-chat",
        # DeepSeek's served models are text-only: they cannot look at the
        # Source Photo, so detection falls back to the offline Spec and the
        # seller supplies the category themselves.
        "vision": False, "free_tier": False,
    },
}


def _pick_provider() -> str:
    """Explicit LLM_PROVIDER wins; otherwise the first provider with a key set."""
    want = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if want in PROVIDERS:
        return want
    for name, p in PROVIDERS.items():
        if os.getenv(p["key_env"]):
            return name
    return "openai"


PROVIDER = _pick_provider()
_P = PROVIDERS[PROVIDER]

# Per-model overrides still win, so a provider's defaults are only a starting point.
MODEL_CONTENT = os.getenv("LLM_MODEL_CONTENT") or os.getenv("OPENAI_MODEL_CONTENT") or _P["content"]
MODEL_FAST = os.getenv("LLM_MODEL_FAST") or os.getenv("OPENAI_MODEL_FAST") or _P["fast"]


def provider_info() -> dict:
    return {
        "provider": PROVIDER,
        "vision": _P["vision"],
        "free_tier": _P["free_tier"],
        "key_env": _P["key_env"],
        "key_present": bool(os.getenv(_P["key_env"])),
        "model_content": MODEL_CONTENT,
        "model_fast": MODEL_FAST,
    }


def supports_vision() -> bool:
    return bool(_P["vision"])

# Longest edge, in px, that images are downscaled to before upload.
MAX_IMAGE_EDGE = int(os.getenv("OPENAI_MAX_IMAGE_EDGE", "1536"))

_client = None
_client_attempted = False

# Circuit breaker. A key that is revoked, unfunded, or wrong authenticates the
# SDK object fine and only fails at call time — so "is a key present" is not the
# same question as "can we actually call the model". Once a call fails for a
# reason retrying cannot fix, this trips and the pipeline uses its offline path
# instead of failing every request in the same way.
_api_dead = False
_api_dead_reason = ""


def api_dead() -> tuple:
    return _api_dead, _api_dead_reason


def _note_failure(exc) -> None:
    global _api_dead, _api_dead_reason
    name = type(exc).__name__
    text = str(exc)
    fatal = ("AuthenticationError", "PermissionDeniedError", "NotFoundError")
    if name in fatal or "invalid_api_key" in text or "insufficient_quota" in text:
        _api_dead = True
        if "invalid_api_key" in text:
            _api_dead_reason = "API key is invalid or revoked"
        elif "insufficient_quota" in text:
            _api_dead_reason = "API account has no remaining quota"
        else:
            _api_dead_reason = f"{name}"
        logger.error(f"model API marked unavailable: {_api_dead_reason}")


def get_client():
    """
    Lazily construct and cache the client for whichever provider is selected.

    All supported providers speak the OpenAI wire format, so this is the same
    SDK pointed at a different base_url.
    """
    global _client, _client_attempted
    if not _client_attempted:
        _client_attempted = True
        try:
            from openai import OpenAI
            key = os.getenv(_P["key_env"])
            if not key:
                logger.error(f"{_P['key_env']} is not set — provider '{PROVIDER}' "
                             "cannot be used; the pipeline will run offline")
                _client = None
            else:
                # An explicit timeout matters more than usual here: some
                # hosted models accept the request and then never answer, and
                # a hung call blocks a generation job for its full 25-minute
                # ceiling. Fail fast and let the offline path take over.
                kwargs = {"api_key": key,
                          "timeout": float(os.getenv("LLM_TIMEOUT", "90")),
                          "max_retries": int(os.getenv("LLM_RETRIES", "1"))}
                if _P["base_url"]:
                    kwargs["base_url"] = _P["base_url"]
                _client = OpenAI(**kwargs)
                logger.info(f"LLM provider: {PROVIDER} "
                            f"(vision={_P['vision']}, models={MODEL_FAST}/{MODEL_CONTENT})")
        except Exception as e:
            logger.error(f"client init failed for provider '{PROVIDER}': {e}")
            _client = None
    return _client


def image_part(path_or_bytes, detail: str = "high") -> Optional[dict]:
    """
    Turn a local image path (or raw bytes) into an OpenAI image content part.

    Downscales to MAX_IMAGE_EDGE and re-encodes as JPEG. Returns None if the
    file can't be decoded, so a single unreadable upload degrades that one
    image rather than failing the whole call.
    """
    try:
        from PIL import Image
        if isinstance(path_or_bytes, (bytes, bytearray)):
            img = Image.open(io.BytesIO(path_or_bytes))
        else:
            img = Image.open(path_or_bytes)

        try:
            img = img.convert("RGB")
            if max(img.size) > MAX_IMAGE_EDGE:
                img.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=88)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        finally:
            img.close()

        return {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": detail},
        }
    except Exception as e:
        logger.warning(f"image_part failed for {path_or_bytes!r}: {e}")
        return None


def _build_messages(system: str, text: str, images=None, detail="high"):
    content = [{"type": "text", "text": text}]
    for im in (images or []):
        part = image_part(im, detail=detail) if not isinstance(im, dict) else im
        if part:
            content.append(part)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]


async def chat_json(
    system: str,
    text: str,
    images=None,
    model: Optional[str] = None,
    temperature: float = 0.8,
    max_tokens: int = 8192,
    detail: str = "high",
) -> Optional[dict]:
    """
    One structured call. Returns a parsed dict, or None on any failure.

    response_format forces valid JSON, so there is no fence stripping and no
    "the model wrote prose around it" failure mode.
    """
    client = get_client()
    if not client:
        return None

    messages = _build_messages(system, text, images, detail)
    mdl = model or MODEL_CONTENT

    async def _call(use_json_mode: bool):
        kwargs = dict(model=mdl, messages=messages,
                      temperature=temperature, max_tokens=max_tokens)
        if use_json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        return await asyncio.to_thread(client.chat.completions.create, **kwargs)

    resp = None
    try:
        resp = await _call(True)
    except Exception as e:
        # Not every OpenAI-compatible provider implements json_object mode.
        # Retry plainly before giving up, but only when the refusal is about
        # that parameter — never swallow an auth or quota failure.
        txt = str(e).lower()
        retryable = ("response_format" in txt or "json_object" in txt
                     or "unsupported" in txt or "not supported" in txt)
        if retryable:
            logger.warning(f"{PROVIDER}: json mode unsupported, retrying plain")
            try:
                resp = await _call(False)
            except Exception as e2:
                _note_failure(e2)
                logger.error(f"chat_json failed ({mdl}): {e2}")
                return None
        else:
            _note_failure(e)
            logger.error(f"chat_json failed ({mdl}): {e}")
            return None

    try:
        choice = resp.choices[0]
        raw = (choice.message.content or "").strip()
    except Exception:
        return None

    # A model that ran out of output budget returns valid-looking but truncated
    # JSON, which then fails to parse and looks like a model failure. Retry once
    # with real headroom rather than reporting a phantom error.
    if getattr(choice, "finish_reason", None) == "length" and max_tokens < 8000:
        logger.warning(f"{PROVIDER}/{mdl}: hit the token ceiling, retrying larger")
        try:
            resp = await asyncio.to_thread(
                client.chat.completions.create, model=mdl, messages=messages,
                temperature=temperature, max_tokens=min(8192, max_tokens * 2),
                response_format={"type": "json_object"})
            raw = (resp.choices[0].message.content or "").strip()
        except Exception as e:
            _note_failure(e)

    try:
        return json.loads(raw)
    except Exception:
        pass
    # Providers without json mode wrap output in fences or prose — recover the
    # outermost JSON object rather than discarding a usable response.
    import re as _re
    cleaned = _re.sub(r"^```[a-zA-Z]*\n?", "", raw, flags=_re.M)
    cleaned = _re.sub(r"\n?```\s*$", "", cleaned, flags=_re.M).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    m = _re.search(r"\{.*\}", cleaned, _re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    logger.error(f"chat_json could not parse output from {PROVIDER}/{mdl}")
    return None


async def chat_text(
    system: str,
    text: str,
    images=None,
    model: Optional[str] = None,
    temperature: float = 0.4,
    max_tokens: int = 8192,
) -> Optional[str]:
    """Same as chat_json but returns raw text — used by the HTML chat-edit path."""
    client = get_client()
    if not client:
        return None
    try:
        resp = await asyncio.to_thread(
            client.chat.completions.create,
            model=model or MODEL_CONTENT,
            messages=_build_messages(system, text, images),
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content
    except Exception as e:
        _note_failure(e)
        logger.error(f"chat_text request failed: {e}")
        return None
