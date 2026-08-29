"""
Share Cards (FR-29) — the image that appears when a Site is shared.

This is not decoration. The PRD's primary metric is SM-1, the share rate, and
§2.3 has the Seller sending their new Site to a WhatsApp broadcast list within
seconds of the Reveal. If that link unfurls as a bare blue URL, the moment the
whole product is engineered around lands flat.

Two sizes, both composed from the Seller's own photo and the derived Grade:

  og      1200x630   link preview in WhatsApp, Messenger, Twitter, Slack
  story   1080x1920  Instagram / WhatsApp Status

Nothing here invents imagery — the product photo is the product photo, cropped
to fit and set on the Site's own ground colour.
"""
import io
import logging
import os
from typing import Optional

logger = logging.getLogger("sharecard")

SIZES = {"og": (1200, 630), "story": (1080, 1920)}


def _font(size: int, bold: bool = False):
    """
    A real typeface where the platform has one, PIL's bitmap default otherwise.

    Deliberately tries system fonts rather than shipping any: the Pack webfonts
    are licensed for web embedding, and rasterising them into a downloadable
    image is a different use.
    """
    from PIL import ImageFont
    candidates = [
        r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            if os.path.isfile(path):
                return ImageFont.truetype(path, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size)
    except Exception:
        return ImageFont.load_default()


def _wrap(draw, text, font, max_width):
    words, lines, cur = (text or "").split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _cover(img, box):
    """Scale-and-crop the photo to fill a box without distorting it."""
    from PIL import Image
    bw, bh = box
    iw, ih = img.size
    scale = max(bw / iw, bh / ih)
    img = img.resize((max(1, int(iw * scale)), max(1, int(ih * scale))), Image.LANCZOS)
    iw, ih = img.size
    left, top = (iw - bw) // 2, (ih - bh) // 2
    return img.crop((left, top, left + bw, top + bh))


def build_card(photo_bytes: bytes, theme: dict, kind: str = "og",
               site_name: str = "", headline: str = "", price: str = "") -> Optional[bytes]:
    """Compose one Share Card. Returns PNG bytes, or None if it cannot be made."""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None

    W, H = SIZES.get(kind, SIZES["og"])
    bg = theme.get("bg", "#faf9f7")
    ink = theme.get("text_main", "#1a1714")
    muted = theme.get("text_muted", "#5c554e")
    accent = theme.get("primary", "#8a5a2b")

    try:
        card = Image.new("RGB", (W, H), bg)
        draw = ImageDraw.Draw(card)

        photo = None
        if photo_bytes:
            try:
                photo = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
            except Exception:
                photo = None

        if kind == "og":
            # Photo fills the left 46%; the words sit on the Grade to its right.
            pw = int(W * 0.46)
            if photo:
                card.paste(_cover(photo, (pw, H)), (0, 0))
            x = pw + 56
            avail = W - x - 56

            f_brand = _font(30, True)
            f_head = _font(52, True)
            f_price = _font(38, True)

            draw.text((x, 92), (site_name or "").upper()[:34], font=f_brand, fill=accent)
            lines = _wrap(draw, headline, f_head, avail)[:4]
            y = 150
            for ln in lines:
                draw.text((x, y), ln, font=f_head, fill=ink)
                y += 62
            if price:
                y = min(y + 26, H - 120)
                draw.text((x, y), price, font=f_price, fill=ink)
            draw.rectangle([x, H - 74, x + 84, H - 68], fill=accent)

        else:
            # Story: tall photo up top, a solid Grade panel beneath it.
            ph = int(H * 0.62)
            if photo:
                card.paste(_cover(photo, (W, ph)), (0, 0))
            else:
                ph = 0
            draw.rectangle([0, ph, W, H], fill=bg)
            x, avail = 84, W - 168

            f_brand = _font(38, True)
            f_head = _font(66, True)
            f_price = _font(50, True)

            y = ph + 76
            draw.text((x, y), (site_name or "").upper()[:30], font=f_brand, fill=accent)
            y += 74
            for ln in _wrap(draw, headline, f_head, avail)[:4]:
                draw.text((x, y), ln, font=f_head, fill=ink)
                y += 80
            if price:
                draw.text((x, y + 28), price, font=f_price, fill=muted)

        out = io.BytesIO()
        card.save(out, format="PNG", optimize=True)
        return out.getvalue()
    except Exception as e:
        logger.warning(f"share card ({kind}) failed: {e}")
        return None


def build_favicon(photo_bytes: bytes, sizes=(32, 180)) -> dict:
    """Square crops of the product photo, for the browser tab and home screen."""
    out = {}
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
        side = min(img.size)
        left, top = (img.size[0] - side) // 2, (img.size[1] - side) // 2
        sq = img.crop((left, top, left + side, top + side))
        for s in sizes:
            buf = io.BytesIO()
            sq.resize((s, s), Image.LANCZOS).save(buf, format="PNG", optimize=True)
            out[s] = buf.getvalue()
        img.close()
    except Exception as e:
        logger.warning(f"favicon build failed: {e}")
    return out
