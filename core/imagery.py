"""
Derived imagery — several distinct looks from the seller's single photo.

The seller uploads one image. Using it once leaves the page thin; using it
unchanged in six slots is what made the first build look broken. So the photo
is re-cut instead: a hero, a tight detail crop, a wide tinted band, and a square
card. Same object, genuinely different compositions, so a page can carry the
product three or four times without ever repeating a frame.

Nothing is invented. Every variant is a crop or a tone of the seller's own
pixels — §5 of the PRD is explicit that the system never fabricates product
imagery.
"""
import io
import logging
from typing import Optional

logger = logging.getLogger("imagery")

# Each crop is cut to the shape of the slot it will actually occupy. Measured
# from the Packs' own stylesheets:
#
#   .shot / .shot-detail   height:auto      -> the image's own ratio is used
#   .hero-under/.plate     330-520px tall, full width  -> wide
#   .tile (tumble 2x2)     aspect-ratio 1/1 -> square
#   .card__photo           170px tall, card width      -> wide-ish
#   .work-shot             180-240px tall               -> wide-ish
#
# Cutting to the slot means the browser's object-fit:cover has nothing left to
# trim, which is what was chopping the product off centre before.
SLOTS = {
    "hero":   0.78,   # portrait column beside the copy
    "wide":   1.70,   # full-width bands, card photos, work shots
    "square": 1.00,   # tiles and thumbnails
}

# The widest each slot is actually drawn at, measured off the Packs' own
# stylesheets on a 1440px screen.
#
# Without these the plate was sized from the cut-out product alone, so a
# 900x1200 photo produced an 848px band that the page then stretched across
# 1350px — the browser doing the enlarging, badly, on the biggest image on the
# page. Composing at the slot's real width moves that work to LANCZOS, and the
# MAX_UPSCALE limit in imagedirector stops it inventing detail that was never
# in the photo.
SLOT_WIDTH = {
    "hero":   760,    # a column, roughly half the content width
    "wide":  1500,    # full-bleed bands — the widest thing on any page
    "square": 820,    # tiles, cards and thumbnails
}

# Kept for callers that still ask by the old names.
VARIANTS = {"hero": (0.78, 1.00), "square": (1.00, 1.02)}


def _crop_to(img, aspect: float, zoom: float):
    """Centre-crop to an aspect ratio, then zoom in by cropping further."""
    from PIL import Image
    iw, ih = img.size

    # target box at this aspect, as large as fits
    if iw / ih > aspect:
        ch = ih
        cw = int(ch * aspect)
    else:
        cw = iw
        ch = int(cw / aspect)

    # zoom by shrinking the box around the same centre
    cw = max(16, int(cw / zoom))
    ch = max(16, int(ch / zoom))

    left = (iw - cw) // 2
    # Bias slightly above centre: products photographed on a surface sit high
    # in frame, and a dead-centre crop tends to cut the top off.
    top = max(0, int((ih - ch) * 0.42))
    return img.crop((left, top, left + cw, top + ch))


def _tint(img, hex_colour: str, strength: float = 0.34):
    """Lay the Grade's own colour over a crop, so a band reads as design."""
    from PIL import Image
    from core.utils import hex_to_rgb
    rgb = hex_to_rgb(hex_colour) or (60, 60, 60)
    layer = Image.new("RGB", img.size, rgb)
    return Image.blend(img.convert("RGB"), layer, strength)


# Below this, cropping throws away pixels the page cannot spare. A 200x150
# upload cropped to a 0.8 portrait becomes 120x150 — smaller than the original
# and then stretched across the page. For small sources the right move is to
# leave the frame alone.
MIN_CROP_EDGE = 900


def derive_variants(photo_bytes: bytes, theme: Optional[dict] = None,
                    max_edge: int = 1600, is_cutout: bool = False) -> dict:
    """
    Returns {name: (bytes, content_type, width, height)} — one entry per slot.

    Two different jobs depending on what came in:

      a background-free cut-out  each slot is COMPOSED from it: a plate at the
                                 slot's own shape with the product centred and
                                 scaled to fill it. That is what makes the
                                 image sit square in its frame instead of
                                 being trimmed by object-fit.

      an ordinary photo          each slot is CROPPED to that shape, so the
                                 browser has nothing left to cut off.

    Never raises — a failure just means fewer variants, and the templates fall
    back to the hero.
    """
    out = {}
    try:
        from PIL import Image
    except Exception:
        return out
    try:
        src = Image.open(io.BytesIO(photo_bytes))
        src = src.convert("RGBA" if is_cutout else "RGB")
    except Exception as e:
        logger.warning(f"derive_variants: unreadable photo ({e})")
        return out

    small = max(src.size) < MIN_CROP_EDGE and not is_cutout
    if small:
        logger.info(f"source is {src.size[0]}x{src.size[1]} — too small to crop, "
                    "using the frame as shot")
    try:
        for name, aspect in SLOTS.items():
            try:
                if is_cutout:
                    from core.imagedirector import compose
                    blob = compose(src, theme or {}, aspect,
                                   target_w=SLOT_WIDTH.get(name))
                    im = Image.open(io.BytesIO(blob))
                    out[name] = (blob, "image/jpeg", im.size[0], im.size[1])
                    im.close()
                    continue
                # A small photo is used whole. Cropping it would discard the
                # few pixels it has and force a larger upscale on the page.
                im = src.copy() if small else _crop_to(src, aspect, 1.0)
                if max(im.size) > max_edge:
                    im.thumbnail((max_edge, max_edge), Image.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=86, optimize=True)
                out[name] = (buf.getvalue(), "image/jpeg", im.size[0], im.size[1])
                im.close()
            except Exception as e:
                logger.warning(f"variant '{name}' skipped: {e}")
    finally:
        src.close()
    return out


def pick_best(photos: list) -> int:
    """
    Index of the photo that should carry the hero.

    Resolution first — a page can place a sharp photo well and can do nothing
    with a soft one — then contrast, which separates a flat snapshot from one
    with some modelling in it.
    """
    best, best_score = 0, -1.0
    for i, blob in enumerate(photos):
        try:
            from PIL import Image, ImageStat
            im = Image.open(io.BytesIO(blob)).convert("RGB")
            try:
                px = max(im.size)
                std = sum(ImageStat.Stat(im).stddev) / 3.0
                score = min(px, 2400) / 2400 * 0.75 + min(std, 80) / 80 * 0.25
            finally:
                im.close()
        except Exception:
            score = 0.0
        if score > best_score:
            best, best_score = i, score
    return best


async def build_image_set(photo_bytes, theme: dict, website_id: str,
                          upload_fn, to_thread, spec=None, quality=None) -> dict:
    """
    Produce the variants and store them. Accepts one photo or a list.

    With several photos the page stops leaning on one frame: the best one
    carries the hero, and the rest become a gallery the sub-pages draw from,
    so About and Work are no longer walls of text.

    Returns {name: url} plus `gallery` (list) and per-image `_w`/`_h`.
    """
    urls = {}
    photos = ([photo_bytes] if isinstance(photo_bytes, (bytes, bytearray))
              else [p for p in (photo_bytes or []) if p])
    if not photos:
        return urls

    # Direct every photo before cropping: the treatment decides whether the
    # background comes out, whether the lamp gets neutralised, and so on. A
    # crop of an untreated photo is just a smaller untreated photo.
    if spec is not None:
        try:
            from core.imagedirector import (plan_treatment, analyze_background,
                                            apply as direct_apply)
            # Measure the lead photo's background, then plan from it. The
            # decision to remove a background is a finding about the photo,
            # not a setting.
            bg = await to_thread(analyze_background, photos[0], theme)
            plan = await to_thread(plan_treatment, spec, quality or {}, theme, bg)
            cut_out = "cutout" in [st["op"] for st in plan]

            # When the background is coming out anyway, try replacing it with
            # an image model first (skills/bg-remover.md). It re-renders the
            # photograph rather than segmenting it, so thin structures — a
            # chair base, a wire handle, a chain — survive, and there is no
            # mask to wrongly INCLUDE a patch of floor.
            #
            # Strictly an upgrade: no key, no quota, or a result that no longer
            # matches the seller's product, and the local cut-out below runs
            # exactly as before. A generation is never blocked on it.
            # Three ways to deal with the background, best first. Each falls
            # through to the next, and the last needs no key at all — so a
            # generation is never blocked, only made better when it can be.
            #
            #   stage    put the product in a setting from its own world.
            #            skills/product-staging.md. Best by a distance, and the
            #            only one that also solves resolution: a staged
            #            photograph is generated at its own size, where a
            #            cut-out is limited to the few hundred pixels the
            #            seller's product actually occupies.
            #   replace  the product on the site's own ground colour. Clean,
            #            but says "photographed against something we removed".
            #   cut out  local rembg. No key, no network, weakest on thin
            #            structures.
            if cut_out:
                from core import bgremover
                staged = False
                try:
                    if bgremover.available():
                        photos = [await to_thread(bgremover.stage, photos[0], spec)]                                  + photos[1:]
                        staged = True
                        urls["_staged"] = True
                        logger.info("product staged in a generated setting")
                except Exception as e:
                    logger.info(f"staging unavailable ({e})")
                if not staged:
                    try:
                        if bgremover.available():
                            photos = [await to_thread(
                                bgremover.replace, photos[0], theme)] + photos[1:]
                            urls["_bg_replaced"] = True
                            logger.info("background replaced by image model")
                            staged = True
                    except Exception as e:
                        logger.info(f"image-model background skipped ({e}); "
                                    "using the local cut-out")
                if staged:
                    # Both return a photograph, not a transparent cut-out, so
                    # the slots crop rather than composing a plate.
                    plan = [st for st in plan if st["op"] != "cutout"]
                    cut_out = False

            if plan:
                treated = []
                for blob in photos:
                    out, applied = await to_thread(
                        direct_apply, blob, plan, theme, cut_out)
                    treated.append(out or blob)
                photos = treated
                # A cut-out is composed per slot below, so it must NOT be
                # cropped like an ordinary photo — cropping a transparent PNG
                # to a slot shape just clips the product.
                urls["_is_cutout"] = cut_out
            urls["_background"] = bg
            urls["_treatment"] = ([st["op"] for st in plan] if plan else [])
            if urls.get("_staged"):
                urls["_treatment"].append("staged")
            elif urls.get("_bg_replaced"):
                urls["_treatment"].append("bg_replaced")
            logger.info(f"background: clutter={bg.get('clutter')} "
                        f"edges={bg.get('edges')} clash={bg.get('clash')} "
                        f"-> removal {'YES' if bg.get('needs_removal') else 'no'}")
            if plan:
                logger.info("director applied: " + ", ".join(st["op"] for st in plan))
        except Exception as e:
            logger.warning(f"image director skipped: {e}")

    # The strongest photo leads; the others fill the pages behind it.
    lead = pick_best(photos)
    rest = [p for i, p in enumerate(photos) if i != lead]
    photo_bytes = photos[lead]

    gallery, gdims = [], []
    for n, blob in enumerate(rest):
        try:
            made = await to_thread(derive_variants, blob, theme, 1400,
                                   urls.get('_is_cutout', False))
            item = made.get("hero") or next(iter(made.values()), None)
            if not item:
                continue
            data, ctype, w, h = item
            gallery.append(await to_thread(
                upload_fn, data, ctype,
                f"websites/{website_id}/assets", f"shot-g{n}.jpg"))
            gdims.append({"w": w, "h": h})
        except Exception as e:
            logger.warning(f"gallery image {n} skipped: {e}")
    if gallery:
        urls["gallery"] = gallery
        urls["gallery_dims"] = gdims
        urls["count"] = len(photos)
    variants = await to_thread(derive_variants, photo_bytes, theme, 1400,
                               urls.get('_is_cutout', False))
    for name, (blob, ctype, w, h) in variants.items():
        try:
            urls[name] = await to_thread(
                upload_fn, blob, ctype,
                f"websites/{website_id}/assets", f"shot-{name}.jpg")
            # The page must know how many real pixels it has to work with.
            urls[name + "_w"] = w
            urls[name + "_h"] = h
        except Exception as e:
            logger.warning(f"upload of variant '{name}' failed: {e}")
    return urls
