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

`sect` has a default. The Pack home templates call it for each section's class
attribute. Without a fallback, any render path that has not planned a
composition dies on `UndefinedError: 'sect' is undefined` and loses the whole
site — which is not hypothetical: a server holding older Python in memory while
Jinja re-read new templates from disk failed exactly that way. A `sect` passed
in the render context still wins, so a planned composition is unaffected.
"""
import os

import jinja2

from core.composition import make_sect

TEMPLATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")

jinja_env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATE_DIR))
jinja_env.globals["sect"] = make_sect({})
jinja_env.globals.setdefault("comp", {})
