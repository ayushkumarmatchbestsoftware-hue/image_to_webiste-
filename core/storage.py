"""
Where a generated site is kept.

Every page, image, share card and content.json a generation produces is written
here and read back from here. It is the one piece of state the service cannot
run without.

One implementation, not an interface with a stand-in behind it. Two versions of
one thing drift, and the drift is invisible until it costs something.

Files land under STORE_DIR, keyed as `websites/<id>/home.html`, so an object
store could be put back under this interface without touching a caller.

  save     write bytes, return the URL a browser can fetch them from
  load     read bytes back by key
  listing  every key under a prefix

What this does not do is span machines. One process on one disk is the whole
story, which is the right shape for a single deployment and the wrong one for
several behind a load balancer.
"""
import logging
import os
import uuid

logger = logging.getLogger("storage")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE_DIR = os.getenv("STORE_DIR", os.path.join(ROOT, "local_store"))

# Where generated pages point for their images.
#
# Relative is the correct default: it resolves against whatever host and port
# is actually serving the page, and a hardcoded absolute URL was once the
# reason every product image on every preview was a broken icon while the file
# sat on disk intact. PUBLIC_BASE_URL makes it absolute again, which real
# hosting needs anyway so a shared link's og:image resolves.
_BASE = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
PUBLIC_URL = os.getenv("MEDIA_URL") or (f"{_BASE}/media" if _BASE else "/media")


class StoreUnwritable(RuntimeError):
    """The store directory cannot be written to. Nothing can be saved."""


def _check_writable() -> None:
    """
    Fail at boot, not at the seller's first order.

    The container runs as an unprivileged uid and STORE_DIR is usually a volume
    bound from the host. If the host directory is owned by someone else the
    service starts perfectly, serves /health, generates a site, and then cannot
    write one byte of it - and the first person to find out is a seller whose
    order vanished. Checking here turns that into a container that refuses to
    start with a message naming the fix.
    """
    probe = os.path.join(STORE_DIR, ".write-probe")
    try:
        os.makedirs(STORE_DIR, exist_ok=True)
        with open(probe, "wb") as fh:
            fh.write(b"x")
        os.remove(probe)
    except OSError as e:
        raise StoreUnwritable(
            f"cannot write to STORE_DIR ({STORE_DIR}): {e}. Every generated "
            f"site, published storefront and customer order is kept there. If "
            f"this is a bind mount, the directory must be writable by the uid "
            f"the container runs as (10001): "
            f"chown -R 10001:10001 <host path>"
        ) from e


_check_writable()


def _path_for(key: str) -> str:
    """
    The file a key names.

    Keys are joined a segment at a time and the result is checked to be inside
    STORE_DIR, so a key carrying `..` cannot reach a file the store does not
    own. Keys come from generated content, and content is not a trusted source.
    """
    dest = os.path.normpath(os.path.join(STORE_DIR, *str(key).split("/")))
    root = os.path.normpath(STORE_DIR)
    if dest != root and not dest.startswith(root + os.sep):
        raise ValueError(f"key escapes the store: {key!r}")
    return dest


def save(data: bytes, content_type: str, folder: str = "websites",
         filename: str = None, file_extension: str = None) -> str:
    """Write bytes and return the URL they can be fetched from."""
    if filename:
        key = f"{folder}/{filename}"
    else:
        ext = file_extension or content_type.split("/")[-1]
        key = f"{folder}/{uuid.uuid4()}.{ext}"
    dest = _path_for(key)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as fh:
        fh.write(data)
    logger.debug(f"wrote {key} ({len(data)} bytes)")
    return f"{PUBLIC_URL}/{key}"


def load(key: str) -> bytes:
    """Read bytes back. Raises FileNotFoundError if the key is not there."""
    src = _path_for(key)
    if not os.path.exists(src):
        raise FileNotFoundError(key)
    with open(src, "rb") as fh:
        return fh.read()


def listing(prefix: str) -> list:
    """Every key under a prefix, in the same form `save` was given."""
    base = _path_for(prefix)
    out = []
    for dirpath, _dirs, files in os.walk(base):
        for f in files:
            full = os.path.join(dirpath, f)
            out.append(os.path.relpath(full, STORE_DIR).replace(os.sep, "/"))
    return out
