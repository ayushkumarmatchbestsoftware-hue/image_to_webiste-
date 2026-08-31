"""
The record of a generated site.

Every site writes a content.json beside its pages holding what it took to build
it — the copy, the Spec, the Grade, the layout, the design, the image URLs.
That file is the record. This reads it back.

It replaces an in-process dictionary that the routes used for the seller's
history, their site's name and which design it is in. That dictionary was
emptied by every restart, which the publish route already had a comment
apologising for: a seller who restarted the server lost the list of everything
they had made, while the sites themselves sat on disk intact.

Nothing here is a cache. Reading a small JSON file per site is cheap at the
scale one seller works at, and being always correct beats being fast when the
alternative is state that silently disagrees with the disk.
"""
import json
import logging
import os
import re

logger = logging.getLogger("sites")

RECORD = "content.json"


def _valid(website_id: str) -> bool:
    """Ids come from URLs, so they are checked before becoming a path."""
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(website_id or "")))


def record(website_id: str) -> dict:
    """One site's record, or {} if it has none — a site built before this."""
    from core.storage import load
    if not _valid(website_id):
        return {}
    try:
        return json.loads(load(f"websites/{website_id}/{RECORD}").decode("utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f"record for {website_id[:8]} unreadable: {e}")
        return {}


def ids() -> list:
    """Every site that has pages, newest first."""
    from core.storage import listing, STORE_DIR
    seen = {}
    try:
        for key in listing("websites"):
            parts = key.split("/")
            if len(parts) < 3 or parts[0] != "websites":
                continue
            wid = parts[1]
            if wid in seen:
                continue
            # The record's own timestamp if it has one, the file's otherwise —
            # a site built before created_at was written still sorts sensibly.
            path = os.path.join(STORE_DIR, "websites", wid, RECORD)
            try:
                seen[wid] = os.path.getmtime(path)
            except OSError:
                seen[wid] = 0.0
    except Exception as e:
        logger.warning(f"could not list sites: {e}")
        return []
    return [w for w, _ in sorted(seen.items(), key=lambda kv: -kv[1])]


def every() -> list:
    """[(website_id, record)] newest first, skipping sites with no record."""
    out = []
    for wid in ids():
        doc = record(wid)
        if doc:
            out.append((wid, doc))
    return out
