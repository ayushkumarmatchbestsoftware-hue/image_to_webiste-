"""
Image director — decides what to DO to the seller's photo, then does it.

Until now the upload was placed on the page as shot. A phone photo of a kurta
on a bedsheet is not a product shot, and no amount of layout hides that. This
module reads the measurements already taken (vision.analyze_quality) and the
Product Spec, decides which treatments the photo actually needs, and applies
them in order.

Nothing here invents product pixels. Every operation is a correction, a crop,
or a composite of the seller's own object onto a ground — §5's rule holds.

    plan_treatment(spec, quality, theme)  ->  [Op, ...] with a reason each
    apply(photo_bytes, plan, theme)       ->  treated JPEG bytes

The tools available differ by machine, so `cutout` picks the best backend it
can find and reports which one it used rather than failing when the best one
is missing.
"""
import io
import logging
from typing import Optional

logger = logging.getLogger("imagedirector")

# ---------------------------------------------------------------------------
# Thresholds — the numbers at which a defect is worth acting on. Deliberately
# slacker than the triage thresholds: triage warns the seller, this fixes what
# it can, and over-correcting a decent photo looks worse than leaving it.
# ---------------------------------------------------------------------------
CAST_FIX = 1.10        # channel-mean ratio above which we neutralise the lamp
DARK_FIX = 105         # mean below which we lift exposure
BRIGHT_FIX = 205       # mean above which we pull it back
FLAT_FIX = 42          # stddev below which we add contrast
TIGHT_CROP = 0.45      # fills_frame below which the product is lost in the frame

# How far a cut-out product may be enlarged when composing its plate. LANCZOS
# holds up to about 1.5x; past that there is no detail left to enlarge and the
# result reads as blur rather than as size. A photo that cannot fill its slot
# therefore yields a SMALLER plate, and the display cap in _hero.html stops the
# page blowing it back up.
MAX_UPSCALE = 1.5


def analyze_background(photo_bytes: bytes, theme: Optional[dict] = None) -> dict:
    """
    Measure the BACKGROUND, so removal is a finding rather than a default.

    Three things decide whether a cutout is worth doing, and none of them are
    the seller's opinion:

      clutter   how much the border of the frame varies. A kurta on a bedsheet
                or a table in a room scores high; the same kurta on a plain
                wall scores near zero and needs nothing doing to it.
      edges     texture in the border region. Separates "busy pattern" from
                "plain but slightly uneven lighting", which variance alone
                confuses.
      clash     how far the existing background sits from the ground colour
                the Site will actually use. A product already on warm cream,
                on a page whose ground is warm cream, gains nothing from being
                cut out and put back on almost the same colour.

    Returns the measurements plus `needs_removal`, so the decision can be read
    back later and argued with.
    """
    out = {"clutter": 0.0, "edges": 0.0, "clash": 0.0, "needs_removal": False}
    try:
        from PIL import Image, ImageFilter, ImageStat
        import numpy as np
    except Exception:
        return out
    try:
        img = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
    except Exception:
        return out
    try:
        img.thumbnail((420, 420), Image.LANCZOS)
        a = np.asarray(img, dtype=np.float32)
        h, w, _ = a.shape
        band = max(6, int(min(h, w) * 0.14))

        # Only the border ring — the middle is the product, and its variance
        # says nothing about the background.
        ring = np.concatenate([
            a[:band].reshape(-1, 3), a[-band:].reshape(-1, 3),
            a[:, :band].reshape(-1, 3), a[:, -band:].reshape(-1, 3)])

        # clutter: spread of the border pixels, normalised
        out["clutter"] = round(float(ring.std(axis=0).mean()) / 64.0, 3)

        # edges: how much detail sits in that ring
        eg = np.asarray(img.convert("L").filter(ImageFilter.FIND_EDGES),
                        dtype=np.float32)
        ering = np.concatenate([
            eg[:band].ravel(), eg[-band:].ravel(),
            eg[:, :band].ravel(), eg[:, -band:].ravel()])
        out["edges"] = round(float(ering.mean()) / 42.0, 3)

        # clash: distance from the ground the Site will use
        if theme:
            from core.utils import hex_to_rgb
            g = hex_to_rgb(theme.get("bg_alt") or theme.get("bg") or "#f4f2ef")
            if g:
                d = np.linalg.norm(ring.mean(axis=0) - np.array(g, dtype=np.float32))
                out["clash"] = round(float(d) / 160.0, 3)

        # Thresholds calibrated against measured backgrounds rather than
        # guessed. Measured (clutter / edges / clash):
        #   solid white    0.000 0.166 0.120   leave alone
        #   soft gradient  0.028 0.161 0.045   leave alone
        #   wood table     0.095 0.292 1.216   remove (clashes badly)
        #   patterned sheet 0.184 0.318 0.392  remove (busy)
        #   cluttered room 0.828 0.201 1.099   remove
        #   solid grey     0.000 0.082 1.211   remove (wrong colour for the page)
        # The gap that matters sits between "soft gradient" and "patterned
        # sheet", so clutter lands at 0.12 and edges at 0.26; clash alone
        # fires at 0.75, which separates a genuinely wrong ground from the
        # ordinary variation of an off-white one.
        out["needs_removal"] = bool(
            out["clutter"] > 0.12 or out["edges"] > 0.26 or out["clash"] > 0.75)
    except Exception as e:
        logger.warning(f"background analysis failed: {e}")
    finally:
        img.close()
    return out


def _backend():
    """The best cutout backend this machine actually has."""
    try:
        import rembg  # noqa: F401
        return "rembg"
    except Exception:
        pass
    return "flood"     # always available: PIL flood-fill from the borders


def plan_treatment(spec: dict, quality: dict, theme: dict,
                   background: Optional[dict] = None) -> list:
    """
    Decide the treatment. Returns a list of {op, reason, ...} in apply order.

    Every entry carries the reason it was chosen so the decision is auditable —
    when a photo comes out wrong it must be possible to see which rule fired.
    """
    plan = []
    q = quality or {}
    spec = spec or {}
    geo = spec.get("geometry") or {}

    mean = q.get("mean", 128)
    std = q.get("std", 60)
    cast = q.get("cast_ratio", 1.0)
    means = q.get("channel_means") or [1, 1, 1]

    # 1. Neutralise the lamp before anything else — every later decision reads
    #    colour, and a tungsten cast poisons all of them.
    if cast >= CAST_FIX:
        plan.append({"op": "white_balance", "gains": means,
                     "reason": f"colour cast {cast:.2f} (lamp, not the product)"})

    # 2. Exposure, then contrast. A dark photo brightened first gives the
    #    contrast step something to work with.
    if mean < DARK_FIX:
        plan.append({"op": "exposure", "factor": min(1.9, DARK_FIX / max(mean, 1)),
                     "reason": f"underexposed (mean {mean})"})
    elif mean > BRIGHT_FIX:
        plan.append({"op": "exposure", "factor": max(0.72, BRIGHT_FIX / mean),
                     "reason": f"overexposed (mean {mean})"})
    if std < FLAT_FIX:
        plan.append({"op": "contrast", "factor": 1.0 + min(0.45, (FLAT_FIX - std) / 70),
                     "reason": f"flat (stddev {std}) — no modelling in the object"})

    # 3. Cutout — but only where it BUYS something. Two questions, both
    #    answered by measurement rather than by default:
    #      can we?    the model's own read of separability
    #      should we? the background measurement above
    #    A clean shot on a plain backdrop already looks like a product shot,
    #    and cutting it out only risks nicking the edges of the object.
    separable = spec.get("background_separable") is not False
    bg = background or {}
    needed = bg.get("needs_removal", False)

    if separable and needed:
        why = []
        if bg.get("clutter", 0) > 0.34: why.append(f"cluttered (clutter {bg['clutter']})")
        if bg.get("edges", 0) > 0.34:   why.append(f"textured (edges {bg['edges']})")
        if bg.get("clash", 0) > 0.62:   why.append(f"clashes with the page (clash {bg['clash']})")
        plan.append({"op": "cutout", "backend": _backend(),
                     "reason": "background " + " and ".join(why)})
        plan.append({"op": "ground", "reason": "place it on the Site's own colour"})
        plan.append({"op": "shadow", "reason": "so the product sits rather than floats"})
    elif separable and not needed:
        logger.info("cutout skipped: background is already clean "
                    f"(clutter {bg.get('clutter')}, edges {bg.get('edges')}, "
                    f"clash {bg.get('clash')})")

    # 4. If the object is small in frame, come in closer. Done last so it
    #    crops the composited result, not the original.
    fills = geo.get("fills_frame")
    if isinstance(fills, (int, float)) and 0 < fills < TIGHT_CROP:
        plan.append({"op": "tighten", "amount": 1.0 + min(0.35, (TIGHT_CROP - fills)),
                     "reason": f"product fills only {int(fills*100)}% of the frame"})

    return plan


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------
def _white_balance(img, means):
    from PIL import Image
    try:
        r, g, b = [max(1e-6, float(c)) for c in means]
    except Exception:
        return img
    grey = (r + g + b) / 3.0
    # Clamped: we are neutralising the lamp, not bleaching a genuinely warm
    # object. A terracotta pot must still come out terracotta.
    gains = [min(1.30, max(0.78, grey / c)) for c in (r, g, b)]
    lut = []
    for gain in gains:
        lut += [min(255, int(i * gain)) for i in range(256)]
    return img.convert("RGB").point(lut)


def _cutout_flood(img, tol=38):
    """
    Border flood-fill cutout. No dependencies.

    Works on exactly the case the app coaches sellers toward — an object on a
    plain surface — and is honest about the rest: if it would remove more than
    four fifths or less than a twentieth of the frame it reports failure rather
    than returning a mangled subject.
    """
    from PIL import Image, ImageDraw, ImageFilter
    w, h = img.size
    work = img.convert("RGB")
    mask = Image.new("L", (w, h), 0)          # 0 = keep

    probe = Image.new("RGB", (w + 2, h + 2), (0, 0, 0))
    probe.paste(work, (1, 1))
    seeds = [(1, 1), (w, 1), (1, h), (w, h),
             (w // 2, 1), (w // 2, h), (1, h // 2), (w, h // 2)]
    marker = (255, 0, 255)
    for s in seeds:
        try:
            ImageDraw.floodfill(probe, s, marker, thresh=tol)
        except Exception:
            pass
    filled = probe.crop((1, 1, w + 1, h + 1))
    px = filled.load()
    mpx = mask.load()
    removed = 0
    for y in range(h):
        for x in range(w):
            if px[x, y] == marker:
                mpx[x, y] = 255
                removed += 1
    frac = removed / float(w * h)
    if frac > 0.82 or frac < 0.05:
        return None, frac                      # ate the subject, or found nothing
    alpha = Image.eval(mask, lambda v: 255 - v).filter(ImageFilter.GaussianBlur(1.4))
    out = work.convert("RGBA")
    out.putalpha(alpha)
    return out, frac


_SESSION = None


def _rembg_session():
    """
    One session for the process.

    rembg builds a fresh ONNX session per call by default, which loads the
    model from disk every time — that was the whole of the 50s, not inference.
    Held here and reused; the first call still pays for the load.
    """
    global _SESSION
    if _SESSION is None:
        from rembg import new_session
        import os
        # u2netp by default. Measured against isnet-general-use on the same photo:
        # isnet ran 3.6s to u2netp's 0.3s, needed a 179MB download, and produced
        # 24.5% partial alpha against 26.5% — twelve times the cost for almost
        # nothing. Both models return a SOFT mask; that is inherent, and it is
        # what _clean_edges deals with. Override with REMBG_MODEL if a
        # particular product defeats the small model.
        _SESSION = new_session(os.getenv("REMBG_MODEL", "u2netp"))
    return _SESSION


def warm():
    """Load the model up front so the first seller does not wait for it."""
    try:
        _rembg_session()
        return True
    except Exception as e:
        logger.warning(f"rembg warm-up skipped: {e}")
        return False


def _despeckle(cut, min_frac=0.004):
    """
    Drop stray islands from the mask.

    The lite model leaves small blobs where the background carried texture, and
    those read as dirt on the page. Anything smaller than min_frac of the frame
    that is not the main subject is removed, keeping the largest component and
    any island above the threshold (a handle, a lid, a second earring).
    """
    try:
        import numpy as np
    except Exception:
        return cut
    from PIL import Image
    a = np.array(cut.split()[-1])
    solid = a > 110
    h, w = solid.shape
    if not solid.any():
        return cut

    labels = np.zeros((h, w), dtype=np.int32)
    cur = 0
    keep_min = int(h * w * min_frac)
    sizes = {}
    ys, xs = np.nonzero(solid)
    for y0, x0 in zip(ys, xs):
        if labels[y0, x0]:
            continue
        cur += 1
        stack = [(y0, x0)]
        labels[y0, x0] = cur
        n = 0
        while stack:
            y, x = stack.pop()
            n += 1
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and solid[ny, nx] and not labels[ny, nx]:
                    labels[ny, nx] = cur
                    stack.append((ny, nx))
        sizes[cur] = n
    if not sizes:
        return cut
    biggest = max(sizes, key=sizes.get)
    keep = {k for k, n in sizes.items() if n >= keep_min or k == biggest}
    mask = np.isin(labels, list(keep))
    a = np.where(mask, a, 0).astype("uint8")
    out = cut.copy()
    out.putalpha(Image.fromarray(a))
    return out


# Where the alpha channel is cut hard and where it is allowed to feather. A
# mask straight out of the model is mostly PARTIAL alpha — measured on a real
# upload: 26.5% of pixels partial against 0.4% fully opaque. Every partial
# pixel still carries the ORIGINAL background colour, so compositing one over
# a dark page paints a pale halo, and a faint one leaves the old shadow behind
# as a grey ghost. Both are exactly what a seller notices.
ALPHA_FLOOR = 60      # below this the pixel was background: drop it entirely


def _clean_edges(cut):
    """
    Drop the ghost the model half-kept, and nothing else.

    A soft mask leaves two faults behind. This fixes one of them, on purpose.

      the ghost   very faint pixels — usually the original shadow. Dropping
                  them outright is always right, and cheap.

      the halo    partly-transparent edge pixels still carrying the old
                  background colour. Left alone here.

    Two earlier attempts at the halo both made things worse, and both are worth
    recording because they look reasonable on paper:

      Pushing mid alphas to fully opaque hardened the edge, but where the model
      had wrongly INCLUDED a pale ellipse of floor under a chair base, it
      turned a faint smudge into a solid white shape. Hardening cannot tell a
      soft edge from a confident mistake.

      Repainting rim pixels with nearby product colour meant dividing by a
      blurred coverage weight, and that weight goes to zero at the outside of
      the rim. Measured: a weight of 0.08 multiplies the colour by 12 and
      clips to white. A white bottle on a pale ground was erased outright and
      the rock beneath it came back with cyan channel-clipping.

    So the alpha is only ever cut at the bottom. It is never promoted, and the
    colour is never touched. A faint halo on a dark ground is a small fault;
    an erased product is not.
    """
    try:
        from PIL import Image
    except Exception:
        return cut
    cut = cut.copy()
    a = cut.split()[-1]
    cut.putalpha(a.point(lambda v: 0 if v < ALPHA_FLOOR else v))
    return cut


def _cutout_rembg(img):
    try:
        from rembg import remove
        cut = remove(img.convert("RGBA"), session=_rembg_session())
        return _clean_edges(cut), None
    except Exception as e:
        logger.warning(f"rembg failed, falling back: {e}")
        return None, None


def _ground(cut, theme, pad=0.10, aspect=None, fill=0.76, target_w=None):
    """
    Composite the cut-out onto the Site's own colour with breathing room.

    Trims to the subject's own bounding box first. Without that the original
    photo's margins survive the cutout and the product ends up marooned in an
    empty frame — which looked worse than the untouched photo did.
    """
    from PIL import Image
    from core.utils import hex_to_rgb
    bg = hex_to_rgb(theme.get("bg_alt") or theme.get("bg") or "#f4f2ef") or (244, 242, 239)
    box = cut.split()[-1].getbbox()
    if box:
        cut = cut.crop(box)
    w, h = cut.size

    if aspect:
        # Build the plate at the slot's own shape and centre the product in it.
        #
        # `fill` adapts: when the product's own shape is close to the slot's,
        # a modest fill leaves a tasteful margin. When they disagree — a tall
        # kurta in a wide band — that same fill leaves the frame mostly empty,
        # so the product is pushed to nearly fill the constraining axis and the
        # margin lands on the other one, where it belongs.
        subject = w / float(h)
        mismatch = abs(subject - aspect) / max(subject, aspect)
        fill = min(0.92, fill + mismatch * 0.30)

        ph = int(max(w / aspect, h) / fill)
        pw = int(ph * aspect)
        if pw < w / fill:
            pw = int(w / fill)
            ph = int(pw / aspect)

        # Size the plate for the slot it will occupy, not merely for the
        # product that happens to be on it.
        #
        # Sizing it from the cutout alone meant a 900x1200 photo produced an
        # 848px-wide band, which the page then stretched across 1350px. The
        # browser did that upscaling, badly, on every full-width hero.
        #
        # So aim at what the slot actually needs — but never scale the product
        # beyond MAX_UPSCALE of its real pixels, because past that there is no
        # detail left to enlarge and a soft image is worse than a small one.
        # A low-resolution photo therefore yields a smaller plate, and the
        # display cap in _hero.html keeps the page from blowing it up again.
        if target_w:
            supportable = int((w / fill) * MAX_UPSCALE)
            want = min(int(target_w), supportable)
            if want > pw:
                ph = int(want / aspect)
                pw = want

        scale = min((pw * fill) / w, (ph * fill) / h)
        cut = cut.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        w, h = cut.size
        plate = Image.new("RGBA", (pw, ph), bg + (255,))
        plate.alpha_composite(cut, ((pw - w) // 2, int((ph - h) * 0.46)))
        return plate

    pw, ph = int(w * (1 + pad * 2)), int(h * (1 + pad * 2))
    plate = Image.new("RGBA", (pw, ph), bg + (255,))
    plate.alpha_composite(cut, (int(w * pad), int(h * pad)))
    return plate


def _shadow(plate, theme):
    """A soft contact shadow under the object so it reads as placed, not pasted."""
    from PIL import Image, ImageFilter
    w, h = plate.size
    alpha = plate.split()[-1]
    sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sh.putalpha(alpha.filter(ImageFilter.GaussianBlur(int(h * 0.030) + 4)))
    dark = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    dark.putalpha(Image.eval(sh.split()[-1], lambda v: int(v * 0.30)))
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.alpha_composite(dark, (0, int(h * 0.022)))
    out.alpha_composite(plate)
    return out


def compose(cut_rgba, theme: dict, aspect: float, target_w: int = None):
    """
    Place an already-cut-out product on a plate of the given shape.

    Called once per slot. Composing once and then re-cropping to a second
    shape would move the product back off centre, which is the whole thing we
    are trying to fix.
    """
    import io as _io
    from PIL import Image
    plate = _ground(cut_rgba, theme, aspect=aspect, target_w=target_w)
    plate = _shadow(plate, theme)
    from core.utils import hex_to_rgb
    bg = hex_to_rgb(theme.get("bg_alt") or "#f4f2ef") or (244, 242, 239)
    flat = Image.new("RGB", plate.size, bg)
    flat.paste(plate, mask=plate.split()[-1])
    buf = _io.BytesIO()
    flat.save(buf, format="JPEG", quality=88, optimize=True)
    return buf.getvalue()


def apply(photo_bytes: bytes, plan: list, theme: dict,
          return_cutout: bool = False) -> tuple:
    """
    Run the plan. Returns (bytes, applied) where `applied` lists what actually
    ran — an op that could not be performed is dropped, never faked.

    With return_cutout the corrected, background-free product comes back as
    RGBA PNG instead of a finished plate, so the caller can compose it into
    each slot at that slot's own proportions.
    """
    from PIL import Image, ImageEnhance
    applied = []
    try:
        img = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
    except Exception as e:
        logger.warning(f"director: unreadable photo ({e})")
        return photo_bytes, applied

    # Cutout cost scales with pixels and no Pack displays the hero wider than
    # ~900px, so working larger bought seconds and nothing else. Measured:
    # 1400px took 42s, 900px takes a fraction of that.
    limit = 900 if any(st["op"] == "cutout" for st in plan) else 1400
    if max(img.size) > limit:
        img.thumbnail((limit, limit), Image.LANCZOS)

    cut = None
    for step in plan:
        op = step["op"]
        try:
            if op == "white_balance":
                img = _white_balance(img, step["gains"]); applied.append(op)
            elif op == "exposure":
                img = ImageEnhance.Brightness(img).enhance(step["factor"]); applied.append(op)
            elif op == "contrast":
                img = ImageEnhance.Contrast(img).enhance(step["factor"]); applied.append(op)
            elif op == "cutout":
                res = None
                if step.get("backend") == "rembg":
                    res, _ = _cutout_rembg(img)
                if res is None:
                    res, frac = _cutout_flood(img)
                    if res is None:
                        logger.info(f"director: cutout skipped (would remove {frac:.0%})")
                        continue
                cut = _despeckle(res)
                applied.append(f"cutout:{step.get('backend')}")
            elif op in ("ground", "shadow", "tighten") and return_cutout:
                continue
            elif op == "ground" and cut is not None:
                cut = _ground(cut, theme, aspect=step.get("aspect")); applied.append(op)
            elif op == "shadow" and cut is not None:
                cut = _shadow(cut, theme); applied.append(op)
            elif op == "tighten":
                src = cut or img
                w, h = src.size
                z = step["amount"]
                cw, ch = int(w / z), int(h / z)
                box = ((w - cw) // 2, int((h - ch) * 0.42))
                src = src.crop((box[0], box[1], box[0] + cw, box[1] + ch))
                if cut is not None:
                    cut = src
                else:
                    img = src
                applied.append(op)
        except Exception as e:
            logger.warning(f"director: op '{op}' failed, skipped ({e})")

    if return_cutout and cut is not None:
        import io as _io
        buf = _io.BytesIO()
        cut.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), applied

    final = cut if cut is not None else img
    if final.mode == "RGBA":
        from PIL import Image as _I
        from core.utils import hex_to_rgb
        bg = hex_to_rgb(theme.get("bg_alt") or "#f4f2ef") or (244, 242, 239)
        flat = _I.new("RGB", final.size, bg)
        flat.paste(final, mask=final.split()[-1])
        final = flat
    buf = io.BytesIO()
    final.convert("RGB").save(buf, format="JPEG", quality=88, optimize=True)
    return buf.getvalue(), applied
