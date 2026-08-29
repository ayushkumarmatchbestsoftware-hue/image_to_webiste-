"""
What this seller's last few sites were built in — so the next one differs.

Choosing the best design for a product is a judgement made about that product
alone, and two honest judgements can land on the same answer: a frying pan and
a set of UI icons both read as technical gear, so both got the release-slip
design and the seller saw one website twice. No per-product decision can prevent
that, because neither decision is wrong. Variety is a property of a SEQUENCE,
and the only way to have it is to remember the sequence.

So this keeps a short ledger per seller: which design each of their sites was
built in. The chooser proposes a ranking, this steps down it until it finds one
the seller has not just been given, and the site is built in that.

Three things it deliberately does NOT do.

It does not override the product. The ranking still comes from the Spec and the
art director; this only declines a repeat, and takes the next best thing rather
than a random one - so the fallback is still a design that suits the product.

It does not make a site unstable. The ledger is keyed by site, so regenerating
an existing site returns the design it already has. A seller who reloads does
not watch their website change.

It does not run out. Only the last (designs - 1) sites are remembered, so there
is always at least one design free and the walk always terminates.

Every failure here is non-fatal. A seller whose site is built in a repeated
design has a website; a seller whose generation died reading a ledger does not.
"""
import json
import logging
import re

logger = logging.getLogger("recents")

PREFIX = "designs/recent"


def _key(user_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(user_id or "anon"))[:64] or "anon"
    return f"{PREFIX}/{safe}.json"


def _load(user_id: str) -> list:
    """The seller's ledger, oldest first. Missing or unreadable reads as empty."""
    from core.r2 import fetch_media_from_r2
    try:
        raw = fetch_media_from_r2(_key(user_id))
        doc = json.loads(raw.decode("utf-8"))
        seen = doc.get("seen")
        return [e for e in seen if isinstance(e, dict) and e.get("pack")] \
            if isinstance(seen, list) else []
    except Exception:
        return []


def _save(user_id: str, seen: list) -> None:
    from core.r2 import upload_media_to_r2
    key = _key(user_id)
    folder, name = key.rsplit("/", 1)
    payload = {
        "$comment": "The designs this seller's recent sites were built in, so "
                    "the next one differs. Safe to delete.",
        "seen": seen,
    }
    upload_media_to_r2(json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       "application/json", folder, name)


def steer(user_id: str, website_id: str, ranked: list) -> tuple:
    """
    The design this site should be built in, given what the seller has seen.

    `ranked` is best-first and must be non-empty. Returns (slug, why) where
    `why` is a plain line for the job log — empty when the first choice stood.
    """
    if not ranked:
        raise ValueError("steer() needs at least one candidate")
    first = ranked[0]
    try:
        from core.packs import PACKS
        keep = max(1, len(PACKS) - 1)

        seen = _load(user_id)
        mine = [e for e in seen if e.get("site") == website_id]
        if mine:
            # This site already exists. Rebuilding it must not change its
            # design under the seller's feet.
            was = mine[-1]["pack"]
            return was, (f"kept {was}: this site was already built in it")

        taken = {e["pack"] for e in seen[-keep:] if e.get("pack")}
        choice = next((s for s in ranked if s not in taken), first)

        seen = seen[-(keep - 1):] if keep > 1 else []
        seen.append({"site": website_id, "pack": choice})
        _save(user_id, seen)

        if choice == first:
            return choice, ""
        return choice, (f"{first} was this seller's last design, so the next "
                        f"best that is not - {choice}")
    except Exception as e:
        # A ledger problem must never cost the seller their website.
        logger.warning(f"design ledger unavailable, keeping {first}: {e}")
        return first, ""
