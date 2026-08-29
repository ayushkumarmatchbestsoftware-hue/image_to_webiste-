"""
Publishing — turning a generated site into one people can actually reach.

Doc §3 asks the platform to handle hosting so the seller never touches a server.
Two targets sit behind one call:

  local    served by this platform at /s/<slug>/. Needs nothing — no account,
           no token, no DNS — so a seller can be live the moment their site is
           generated. This is the default.
  vercel   handed to services/vercel_service.py when VERCEL_TOKEN is set, for a
           real CDN and a real domain.

The part that is easy to get wrong:

  A published page must be told where the shop API lives.

The preview works out its own website id from the URL and calls /api/... beside
itself, which is fine while the page and the API share an origin. A published
site does not: on Vercel the page is on vercel.app and a relative /api/... hits
Vercel, where nothing is listening, and every Buy button dies silently. So
publishing injects an absolute base into the page. Get this wrong and the site
looks perfect and sells nothing.
"""
import html
import json
import logging
import os
import re
import shutil
import time

logger = logging.getLogger("publish")

PUBLISH_DIR = os.getenv(
    "PUBLISH_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "local_store", "published"))

# Where a published page should call back to. Must be absolute and reachable
# from a buyer's phone — "localhost" is only ever right while testing on the
# same machine.
PUBLIC_BASE = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

PAGES = ("home.html", "about.html", "services.html", "portfolio.html", "contact.html")

_RESERVED = {"api", "s", "shop", "preview", "media", "download", "health",
             "static", "admin", "www", "assets"}


class PublishError(Exception):
    """Publishing could not complete."""


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)[:40].strip("-")
    return s or "shop"


def _taken(slug: str) -> bool:
    return slug in _RESERVED or os.path.isdir(os.path.join(PUBLISH_DIR, slug))


def claim_slug(site_name: str, website_id: str) -> str:
    """
    A stable, readable address for this site.

    Reuses the slug this site already holds, so re-publishing after an edit
    keeps the URL the seller has already put on a card or a WhatsApp status —
    changing it silently would break every link they have shared.
    """
    existing = _slug_of(website_id)
    if existing:
        return existing
    base = slugify(site_name)
    slug = base
    n = 2
    while _taken(slug):
        slug = f"{base}-{n}"
        n += 1
    return slug


def _slug_of(website_id: str) -> str:
    try:
        for entry in os.listdir(PUBLISH_DIR):
            meta = os.path.join(PUBLISH_DIR, entry, "_publish.json")
            if os.path.exists(meta):
                with open(meta, encoding="utf-8") as fh:
                    if json.load(fh).get("website_id") == website_id:
                        return entry
    except OSError:
        pass
    return ""


# A marker element, not a bare string. The first version guarded on
# "window.__SHOP__" appearing anywhere in the page — but the storefront's own
# script READS that variable and names it in a comment, so the guard matched on
# every page and the config was never injected. The site published looking
# perfect with a cart that never appeared.
CONFIG_ID = "shop-config"


def _rewrite_media(page_html: str) -> str:
    """
    Point asset URLs at wherever this site is actually served from.

    Generation stamps absolute media URLs from the storage base, which in the
    local stack is a fixed 127.0.0.1:5000 whatever port the server is really on.
    Published pages inherited those and every product image came out broken. A
    relative path is correct here and survives a port or host change.
    """
    try:
        from core.r2 import R2_PUBLIC_URL
    except Exception:
        return page_html
    base = (R2_PUBLIC_URL or "").rstrip("/")
    if not base:
        return page_html
    return page_html.replace(base, f"{PUBLIC_BASE}/media" if PUBLIC_BASE else "/media")


def _shop_config(website_id: str, api_base: str) -> str:
    """
    The block that tells a published page where its shop lives.

    JSON-encoded then HTML-escaped for '<' so the values cannot break out of the
    script element, even though both are values this system generated.
    """
    cfg = json.dumps({"site": website_id, "api": api_base or "/api"})
    return (f'<script id="{CONFIG_ID}">window.__SHOP__=' +
            cfg.replace("<", "\\u003c") + ";</script>\n")


def _inject(page_html: str, website_id: str, api_base: str) -> str:
    page_html = _rewrite_media(page_html)
    if f'id="{CONFIG_ID}"' in page_html:
        return page_html
    block = _shop_config(website_id, api_base)
    if "</head>" in page_html:
        return page_html.replace("</head>", block + "</head>", 1)
    return block + page_html


def publish_local(website_id: str, site_name: str, pages: dict,
                  api_base: str = "") -> dict:
    """
    Write the site into the published directory under a readable slug.

    `pages` is {filename: html}. Returns the publish record.
    """
    slug = claim_slug(site_name, website_id)
    target = os.path.join(PUBLISH_DIR, slug)
    tmp = target + ".new"

    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    try:
        base = api_base or (PUBLIC_BASE + "/api" if PUBLIC_BASE else "/api")
        written = []
        for name, content in (pages or {}).items():
            if not name.endswith(".html"):
                continue
            with open(os.path.join(tmp, os.path.basename(name)), "w",
                      encoding="utf-8") as fh:
                fh.write(_inject(content, website_id, base))
            written.append(name)
        if not written:
            raise PublishError("there are no pages to publish")

        record = {
            "website_id": website_id,
            "slug": slug,
            "site_name": site_name,
            "pages": sorted(written),
            "api_base": base,
            "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "target": "local",
        }
        with open(os.path.join(tmp, "_publish.json"), "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=1)

        # Swap the new directory in only once it is complete, so a visitor
        # never lands on a half-written site.
        old = target + ".old"
        shutil.rmtree(old, ignore_errors=True)
        if os.path.isdir(target):
            os.replace(target, old)
        os.replace(tmp, target)
        shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    record["url"] = f"{PUBLIC_BASE}/s/{slug}/" if PUBLIC_BASE else f"/s/{slug}/"
    logger.info(f"published {website_id[:8]} -> {record['url']} "
                f"({len(record['pages'])} pages, api {record['api_base']})")
    return record


def get_published(slug: str) -> dict:
    meta = os.path.join(PUBLISH_DIR, slug, "_publish.json")
    try:
        with open(meta, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def read_page(slug: str, filename: str) -> str:
    """
    Read one published page. Refuses anything that is not a plain .html name in
    this slug's own directory — a published path comes straight off the URL.
    """
    name = os.path.basename(filename or "home.html") or "home.html"
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.html", name):
        raise PublishError("not a page")
    root = os.path.realpath(os.path.join(PUBLISH_DIR, slug))
    path = os.path.realpath(os.path.join(root, name))
    if not path.startswith(root + os.sep):
        raise PublishError("not a page")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def list_published() -> list:
    out = []
    try:
        for entry in sorted(os.listdir(PUBLISH_DIR)):
            rec = get_published(entry)
            if rec:
                rec["url"] = f"{PUBLIC_BASE}/s/{entry}/" if PUBLIC_BASE else f"/s/{entry}/"
                out.append(rec)
    except OSError:
        pass
    return out


def unpublish(slug: str) -> bool:
    """Take a site down. The directory goes; orders and catalogue are untouched."""
    d = os.path.join(PUBLISH_DIR, slug)
    if not os.path.isdir(d):
        return False
    shutil.rmtree(d, ignore_errors=True)
    logger.info(f"unpublished /s/{slug}/")
    return True


def vercel_available() -> bool:
    return bool(os.getenv("VERCEL_TOKEN"))
