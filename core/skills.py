"""
Skill files — the design criteria, as editable text rather than Python.

Everything the Art Director knows about what makes a page good lives in
skills/*.md, not in a prompt buried in a module. Editing one changes behaviour
on the next generation with no code change and no restart: files are re-read
whenever their mtime moves.

A missing skill file is not fatal. The caller gets an empty string and decides
what that means — for the Art Director it means skipping the agent and using
the deterministic rules, which is the right way for this to degrade.
"""
import logging
import os

logger = logging.getLogger("skills")

SKILLS_DIR = os.getenv(
    "SKILLS_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills"))

_cache: dict = {}


def load(name: str) -> str:
    """
    Return the text of skills/<name>.md, or "" if it is missing or unreadable.

    Cached on mtime, so a skill edited while the server runs takes effect on the
    next generation — which is the whole point of these being files.
    """
    path = os.path.join(SKILLS_DIR, f"{name}.md")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        if name not in _cache:
            logger.warning(f"skill '{name}' not found at {path}")
            _cache[name] = (None, "")
        return ""

    cached_mtime, text = _cache.get(name, (None, ""))
    if cached_mtime == mtime:
        return text
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        _cache[name] = (mtime, text)
        logger.info(f"skill '{name}' loaded ({len(text)} chars)")
        return text
    except OSError as e:
        logger.warning(f"skill '{name}' unreadable: {e}")
        return ""


def available() -> list:
    """Every skill name present on disk. Used by /health so the UI can show them."""
    try:
        return sorted(f[:-3] for f in os.listdir(SKILLS_DIR) if f.endswith(".md"))
    except OSError:
        return []
