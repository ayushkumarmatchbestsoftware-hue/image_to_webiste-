"""
Single-document bundler — one self-contained .html file.

The stored page pulls its stylesheet from /packs/<slug>/css and its photo from
the media server, so on someone else's machine it is a broken layout with a
missing image. This flattens all of that into ONE document:

    <style>  the Pack's CSS, with every url() turned into a data: URI
    <img>    the seller's photo as a data: URI
    fonts    embedded in the same way

The result is a single .html file. Double-click it, Chrome renders it exactly
as the preview did — no folder, no server, no internet.

build_single_html()  -> str    the one document
build_download_zip() -> bytes  that document + the original photo alongside it
"""
import base64
import io
import mimetypes
import os
import re
import zipfile
from typing import Optional

from core.utils import clean_editor_artifacts

# Chrome will not decode an inline font it cannot type, and unknown types
# silently fail, so keep an explicit map.
_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf",
    ".eot": "application/vnd.ms-fontobject", ".otf": "font/otf",
}


def _data_uri(path: str) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    ext = os.path.splitext(path)[1].lower()
    mime = _MIME.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"
    try:
        with open(path, "rb") as fh:
            return f"data:{mime};base64," + base64.b64encode(fh.read()).decode("ascii")
    except Exception:
        return None


def _inline_css_urls(css: str, css_dir: str) -> str:
    """
    Replace every url(...) inside a stylesheet with a data: URI.

    Resolves relative to the stylesheet's own directory, which is what the
    browser would do. Anything that cannot be resolved is dropped to `none`
    rather than left pointing at a path that will 404 from a file:// page.
    """
    def repl(m):
        raw = m.group(1).strip().strip('"').strip("'")
        if raw.startswith(("data:", "http://", "https://", "//")):
            return f"url({raw})"
        target = os.path.normpath(os.path.join(css_dir, raw.split("?")[0].split("#")[0]))
        uri = _data_uri(target)
        return f"url({uri})" if uri else "none"

    return re.sub(r"url\(\s*([^)]+?)\s*\)", repl, css)


def build_single_html(html: str, pack_slug: str, packs_root: str,
                      product_images: dict, editable: bool = False) -> str:
    """
    html            the rendered page as stored
    product_images  {absolute_url: raw_bytes}
    editable        keep contenteditable so the downloaded file is still
                    editable in the browser; default strips it
    """
    pack_dir = os.path.join(packs_root, pack_slug)

    # ---- 1. inline every stylesheet the page links, in document order ----
    def link_repl(m):
        tag = m.group(0)
        href = re.search(r'href\s*=\s*["\']([^"\']+)["\']', tag)
        if not href:
            return tag
        h = href.group(1)
        if h.startswith(("http://", "https://", "data:")):
            return tag
        rel = h.split("?")[0].lstrip("/")
        rel = re.sub(rf"^packs/{re.escape(pack_slug)}/", "", rel)
        target = os.path.normpath(os.path.join(pack_dir, rel))
        if not os.path.isfile(target):
            return ""
        try:
            css = open(target, "r", encoding="utf-8", errors="ignore").read()
        except Exception:
            return ""
        media = re.search(r'media\s*=\s*["\']([^"\']+)["\']', tag)
        css = _inline_css_urls(css, os.path.dirname(target))
        if media:
            css = f"@media {media.group(1)} {{\n{css}\n}}"
        return f"<style>\n{css}\n</style>"

    html = re.sub(r'<link\b[^>]*rel\s*=\s*["\']stylesheet["\'][^>]*>', link_repl, html,
                  flags=re.I)

    # ---- 2. inline url() inside any <style> block already in the page ----
    def style_repl(m):
        return m.group(1) + _inline_css_urls(m.group(2), pack_dir) + m.group(3)

    html = re.sub(r"(<style[^>]*>)(.*?)(</style>)", style_repl, html,
                  flags=re.S | re.I)

    # ---- 3. inline the seller's photo(s) ----
    for url, data in (product_images or {}).items():
        ext = os.path.splitext(url.split("?")[0])[1].lower() or ".jpg"
        mime = _MIME.get(ext, "image/jpeg")
        uri = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
        html = html.replace(url, uri)

    # ---- 4. any remaining absolute pack asset in the markup ----
    def src_repl(m):
        rel = m.group(2).lstrip("/")
        rel = re.sub(rf"^packs/{re.escape(pack_slug)}/", "", rel)
        uri = _data_uri(os.path.normpath(os.path.join(pack_dir, rel)))
        return f'{m.group(1)}="{uri}"' if uri else f'{m.group(1)}=""'

    html = re.sub(r'(src|href)\s*=\s*["\'](/packs/[^"\']+)["\']', src_repl, html)

    # ---- 5. inter-page links ----
    html = re.sub(r'(["\'])/preview/[0-9a-zA-Z_\-]+/', r'\1', html)
    html = re.sub(r'(["\'])home\.html', r'\1index.html', html)

    if not editable:
        html = clean_editor_artifacts(html)

    return html


def build_download_zip(docs, product_images: dict,
                       site_name: str = "website") -> bytes:
    """
    The download: every page as its own self-contained document, plus the
    seller's original photo as a real file so they still have it full quality.

    `docs` is {filename: html}. Each page carries its own CSS, fonts and any
    image inline, and links between them are plain filenames — so the folder
    browses correctly with no server and no internet.
    """
    if isinstance(docs, str):          # single-page callers
        docs = {"index.html": docs}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, html in docs.items():
            z.writestr(name, html)
        for i, (url, data) in enumerate((product_images or {}).items()):
            ext = os.path.splitext(url.split("?")[0])[1] or ".jpg"
            z.writestr(f"your-photo{'' if i == 0 else i}{ext}", data)
        z.writestr("README.txt",
                   "Your website\n"
                   "============\n\n"
                   "index.html      Double-click it. Opens in Chrome exactly as you\n"
                   "                saw it - the design, the fonts and your photo are\n"
                   "                all inside this one file. No internet needed, and\n"
                   "                nothing else has to sit next to it.\n\n"
                   "your-photo.*    The original photo you uploaded, full quality.\n\n"
                   "To change the words: go back to the builder, press Edit page,\n"
                   "make your changes, press Save, then download again. Editing the\n"
                   "downloaded file directly means your changes live only on this\n"
                   "computer.\n\n"
                   "To publish: upload index.html to any web host, or email it to\n"
                   "someone - it works as a single attachment.\n")
    buf.seek(0)
    return buf.read()
