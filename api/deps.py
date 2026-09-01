"""
Things more than one router needs.

Kept small on purpose: a shared module that accumulates everything becomes the
same problem as the single large file it was split out of.
"""
import logging
import os
import time

from fastapi import Request

log = logging.getLogger("server")

# The pipeline skips credit deduction for exactly this id (generation.py, step 11).
DEV_USER_ID = "00000000-0000-0000-0000-000000000001"


# ── Rate limiting ─────────────────────────────────────────────────────────
# Two buckets, because they defend against different things and charging both
# to one counter punishes the wrong person: a buyer who mistypes their phone
# number three times is not an attacker, but the first version counted their
# failed attempts and locked them out of their own checkout.
#
#   orders    successful creates — these write to disk, so ration them tightly
#   attempts  every request including rejects — a looser ceiling that still
#             stops someone simply flooding the endpoint
_RATE = {"orders": {}, "attempts": {}}
RATE_MAX = int(os.getenv("SHOP_RATE_MAX", "12"))
ATTEMPT_MAX = int(os.getenv("SHOP_ATTEMPT_MAX", "60"))
RATE_WINDOW = int(os.getenv("SHOP_RATE_WINDOW", "300"))


def rate_ok(bucket: str, key: str, cap: int, record: bool = True) -> bool:
    now = time.time()
    hits = [t for t in _RATE[bucket].get(key, []) if now - t < RATE_WINDOW]
    ok = len(hits) < cap
    if ok and record:
        hits.append(now)
    _RATE[bucket][key] = hits
    return ok


def merchant_ok(website_id: str, request: Request) -> bool:
    """
    Guard for endpoints that change an order.

    Disabled unless SHOP_REQUIRE_KEY=1 so local work stays frictionless. On a
    deployed instance that default would let anyone holding a site id mark
    orders shipped or cancelled, so docker-compose sets it to 1.
    """
    if os.getenv("SHOP_REQUIRE_KEY", "0") not in ("1", "true", "True"):
        return True
    from core import commerce
    want = (commerce.get_settings(website_id) or {}).get("merchant_key", "")
    got = request.headers.get("x-merchant-key", "")
    return bool(want) and got == want


def summary_display(website_id: str) -> dict:
    from core import commerce
    s = commerce.summary(website_id)
    s["revenue_display"] = commerce.format_money(s["revenue_minor"], s["currency"])
    s["committed_display"] = commerce.format_money(s["committed_minor"], s["currency"])
    return s
