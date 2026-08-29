"""
The Jinja environment every page is rendered through.

This lived inside core/generation.py, which meant importing six lines of
template setup pulled in Mongo, Redis, telemetry, the Vercel deployer and the
credits stack — none of which rendering needs. Everything that renders a page
now imports this instead.

Two details here are load-bearing.

Autoescaping is OFF. Starlette's Jinja2Templates turns it on; these templates
are authored assuming raw interpolation, and exactly one `| safe` exists across
all of them. Rendering them with autoescape on silently HTML-escapes every
generated string, so a headline arrives as &amp;quot;… on the page.

`sect` and `t` have defaults. The Pack templates call `sect` for each section's
class attribute and `t` for every fixed interface word, and neither belongs to
the page's content — they come from the composition planner and the language
layer. Any render path that skips either dies on `UndefinedError` and loses the
whole site, which is not hypothetical: a server holding older Python in memory
while Jinja re-read new templates from disk failed exactly that way on `sect`,
and a caller that rendered a Pack without a language context failed the same
way on `t`. Both defaults are overridden by anything passed in the render
context, so a planned composition and a translated interface are unaffected.
"""
import os

import jinja2

from core.composition import make_sect

TEMPLATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")

jinja_env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATE_DIR))
jinja_env.globals["sect"] = make_sect({})
jinja_env.globals.setdefault("comp", {})


def _default_t(key: str, default: str = "") -> str:
    """
    The English interface word for a key.

    Read lazily and cached by core.i18n, so importing the renderer does not
    touch the filesystem, and a missing key falls through to the key itself
    rather than rendering an empty button.
    """
    try:
        from core.i18n import source_strings
        return source_strings().get(key) or default or key
    except Exception:
        return default or key


jinja_env.globals["t"] = _default_t
