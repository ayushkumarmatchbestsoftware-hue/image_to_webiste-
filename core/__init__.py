"""
The generation core.

The only thing this package does on import is teach Pillow to open the format
most sellers' photographs are actually in.

config.py has always accepted .heic and .heif uploads, and Pillow cannot open
either without pillow-heif registered - which nothing did. An iPhone photograph
is HEIC by default, so the most common upload this product will ever receive
passed the extension check and then failed to open, which is the worst possible
place to fail: after the seller has waited.

Registered here rather than in the web layer because every module that opens a
photograph - imagery, imagedirector, bgremover, llm - lives in this package,
and a caller using the pipeline without the API deserves the same support.

Missing pillow-heif is not fatal. Every other format still works; only HEIC
stops, and it says so once rather than failing per photograph with a confusing
Pillow error.
"""
import logging

logger = logging.getLogger("core")

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception as e:  # noqa: BLE001 - any failure here must not stop import
    logger.warning(
        f"HEIC/HEIF photographs cannot be opened ({e}). Most phones shoot HEIC "
        "by default; install pillow-heif, or drop heic/heif from "
        "Config.ALLOWED_EXTENSIONS so the upload is refused with a clear "
        "message instead of failing later."
    )
