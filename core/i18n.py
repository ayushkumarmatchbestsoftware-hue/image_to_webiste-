"""
Language — the site's own words, and the interface around them.

Two different things have to be in the seller's language, and they come from
different places:

  the COPY      headline, descriptions, reviews. Written by the model, so the
                language is an instruction in the prompt.
  the CHROME    "Cart", "Checkout", "Reviews", "Place order". Fixed strings in
                the templates, which is why they were all English no matter
                what language the copy came back in.

This handles the chrome. locales/en.json holds the source strings; every other
language is translated from it ONCE and cached beside it, so a seller can ask
for any language rather than picking from a list somebody hardcoded. A language
already on disk costs nothing.

Three things follow from the language and must not be forgotten, because each
of them breaks a page badly on its own:

  direction     Arabic, Hebrew, Urdu and Farsi read right to left. Without
                dir="rtl" the layout is not merely untranslated, it is wrong.
  script        Devanagari, Arabic and CJK are not in a Latin font. A page set
                in a Latin-only face renders the whole of Hindi as boxes.
  the lang tag  screen readers and search engines both read it.
"""
import json
import logging
import os
import re

logger = logging.getLogger("i18n")

LOCALES_DIR = os.getenv(
    "LOCALES_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "locales"))
SOURCE = "en"

# Scripts that read right to left. Getting this wrong does not produce a
# slightly odd page; it produces an unusable one.
RTL = {"ar", "he", "fa", "ur", "ps", "sd", "yi", "dv"}

# A Latin-only font renders Devanagari or Arabic as empty boxes, so the display
# and body faces have to follow the script. Google Fonts names, since that is
# the one font host the pages already load from.
SCRIPT_FONTS = {
    "deva": ("Tiro Devanagari Hindi", "Noto Sans Devanagari"),
    "arab": ("Noto Naskh Arabic", "Noto Sans Arabic"),
    "beng": ("Tiro Bangla", "Noto Sans Bengali"),
    "taml": ("Tiro Tamil", "Noto Sans Tamil"),
    "telu": ("Ramaraja", "Noto Sans Telugu"),
    "guru": ("Noto Serif Gurmukhi", "Noto Sans Gurmukhi"),
    "gujr": ("Noto Serif Gujarati", "Noto Sans Gujarati"),
    "knda": ("Noto Serif Kannada", "Noto Sans Kannada"),
    "mlym": ("Noto Serif Malayalam", "Noto Sans Malayalam"),
    "cyrl": ("Noto Serif", "Noto Sans"),
    "hans": ("Noto Serif SC", "Noto Sans SC"),
    "hant": ("Noto Serif TC", "Noto Sans TC"),
    "jpan": ("Noto Serif JP", "Noto Sans JP"),
    "kore": ("Noto Serif KR", "Noto Sans KR"),
    "thai": ("Noto Serif Thai", "Noto Sans Thai"),
}

# Which script a language is written in. Only languages whose script is NOT
# Latin need an entry — everything else keeps the Pack's own typefaces, which
# is the point: an English or Spanish site should look exactly as designed.
LANG_SCRIPT = {
    "hi": "deva", "mr": "deva", "ne": "deva", "sa": "deva",
    "bn": "beng", "as": "beng",
    "ta": "taml", "te": "telu", "pa": "guru", "gu": "gujr",
    "kn": "knda", "ml": "mlym",
    "ar": "arab", "ur": "arab", "fa": "arab", "ps": "arab", "sd": "arab",
    "ru": "cyrl", "uk": "cyrl", "bg": "cyrl", "sr": "cyrl",
    "zh": "hans", "zh-tw": "hant", "ja": "jpan", "ko": "kore", "th": "thai",
}

_cache: dict = {}


def normalise(code: str) -> str:
    """A bare language tag: 'HI', 'hi-IN', ' hi ' all become 'hi'."""
    c = re.sub(r"[^a-zA-Z-]", "", str(code or "")).lower().strip("-")
    if not c:
        return SOURCE
    # zh-tw is a genuinely different script; every other region is dropped.
    return c if c in ("zh-tw", "zh-hant") else c.split("-")[0]


def is_rtl(code: str) -> bool:
    return normalise(code) in RTL


def fonts_for(code: str):
    """(display, body) Google Font names, or None to keep the Pack's own."""
    return SCRIPT_FONTS.get(LANG_SCRIPT.get(normalise(code), ""))


def _path(code: str) -> str:
    return os.path.join(LOCALES_DIR, f"{normalise(code)}.json")


def _read(code: str) -> dict:
    try:
        with open(_path(code), encoding="utf-8") as fh:
            return {k: v for k, v in json.load(fh).items()
                    if not k.startswith("$")}
    except (OSError, ValueError):
        return {}


def source_strings() -> dict:
    return _read(SOURCE)


def available() -> list:
    """Languages already on disk. Any other is translated on first use."""
    try:
        return sorted(f[:-5] for f in os.listdir(LOCALES_DIR)
                      if f.endswith(".json"))
    except OSError:
        return [SOURCE]


def _run(coro):
    """
    Await a coroutine from wherever this is called.

    Reached from two places: a plain script, and the middle of the async
    generation pipeline. asyncio.run() only works in the first; in the second
    it raises "cannot be called from a running event loop", which the fallback
    caught and quietly left every interface in English while the copy came back
    correctly translated. A page whose headline is Hindi and whose buttons are
    English looks broken in a way the seller cannot fix.

    When a loop is already running, the coroutine goes to a fresh one on its
    own thread.
    """
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


_TRANSLATE_SYSTEM = """You translate the interface of a small online shop.

You are given a JSON object of English interface strings. Return the SAME keys
with values translated into the requested language.

Rules:
- Translate what a shopper would actually see on a shop, not literal English.
  "Cart" is whatever that country's shops put on that button.
- Keep it short. These are buttons and labels, and a long one breaks a layout.
- Keep the register plain and direct. No marketing tone.
- Leave a value in English if that is genuinely what shops there use.
- Return every key. Never add, drop or rename one."""


def strings(code: str) -> dict:
    """
    The interface strings for a language.

    English is read from disk. Anything else is read from its cached file, and
    translated once if there is no cache — so the first site in a new language
    pays for it and every site after is free.

    Any failure falls back to English. A shop with English buttons and correct
    copy is usable; a shop with missing labels is not.
    """
    lang = normalise(code)
    if lang in _cache:
        return _cache[lang]

    src = source_strings()
    if lang == SOURCE or not src:
        _cache[lang] = src
        return src

    have = _read(lang)
    # A cached file from an older release can be missing newly added keys.
    # Filling the gaps from English beats showing a blank button.
    if have and not (set(src) - set(have)):
        _cache[lang] = have
        return have

    try:
        from core.llm import chat_json, MODEL_FAST, api_dead
        dead, _ = api_dead()
        if dead:
            raise RuntimeError("model unavailable")
        out = _run(chat_json(
            system=_TRANSLATE_SYSTEM,
            text=f"Language: {lang}\n\n{json.dumps(src, ensure_ascii=False)}",
            model=MODEL_FAST, temperature=0.2, max_tokens=2400))
        got = {k: str(v) for k, v in (out or {}).items() if k in src and v}
        if len(got) < len(src) * 0.6:
            raise RuntimeError(f"only {len(got)}/{len(src)} keys returned")
        merged = {**src, **have, **got}
        _write(lang, merged)
        _cache[lang] = merged
        logger.info(f"translated the interface into '{lang}' "
                    f"({len(got)}/{len(src)} keys) and cached it")
        return merged
    except Exception as e:
        logger.warning(f"interface stays English for '{lang}': {e}")
        merged = {**src, **have}
        _cache[lang] = merged
        return merged


def _write(code: str, data: dict) -> None:
    try:
        os.makedirs(LOCALES_DIR, exist_ok=True)
        payload = {"$comment": f"Translated from en.json. Delete this file to "
                               f"have it written again.", **data}
        with open(_path(code), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning(f"could not cache '{code}': {e}")


def context(code: str) -> dict:
    """
    Everything a template needs, ready to drop into the render context.

    `t` is a lookup that falls back to English and then to the key itself, so a
    missing string shows something readable rather than an empty element.
    """
    lang = normalise(code)
    table = strings(lang)
    src = source_strings()

    def t(key: str, default: str = "") -> str:
        return table.get(key) or src.get(key) or default or key

    fonts = fonts_for(lang)
    return {
        "lang": lang,
        "site_lang": lang,
        "dir": "rtl" if is_rtl(lang) else "ltr",
        "is_rtl": is_rtl(lang),
        "t": t,
        "lang_display_font": fonts[0] if fonts else None,
        "lang_body_font": fonts[1] if fonts else None,
    }
