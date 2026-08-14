import io
import os
import re
import json
import tempfile
import shutil
import asyncio
import traceback
import urllib.parse
import uuid as pyuuid
# NOTE: PIL is intentionally NOT imported at module level — it's only needed
# by compress_image() below, during an actual deploy, so it's imported
# lazily right at that point of use instead (see run_vercel_deployment).

from core.r2 import upload_media_to_r2, fetch_media_from_r2, R2_PUBLIC_URL
from core.mongo import get_website_layout
from core.utils import clean_editor_artifacts

async def run_vercel_deployment(website_id: str):
    """
    Core deployment logic moved here to run in the background worker.
    """
    vercel_token = os.getenv("VERCEL_TOKEN")
    if not vercel_token:
        raise Exception("VERCEL_TOKEN is not configured on the server.")

    base_url = (R2_PUBLIC_URL or "").rstrip("/")

    # Compress helper
    def compress_image(raw_bytes: bytes, max_width: int = 1920, quality: int = 82) -> bytes:
        try:
            from PIL import Image  # lazy import — only needed for an actual deploy
            img = Image.open(io.BytesIO(raw_bytes))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGBA")
            else:
                img = img.convert("RGB")
            if img.width > max_width:
                ratio = max_width / img.width
                img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)
            out = io.BytesIO()
            img.save(out, format="WEBP", quality=quality, method=4)
            return out.getvalue()
        except Exception:
            return raw_bytes

    def url_to_object_key(url: str) -> str | None:
        if not base_url: return None
        url = url.strip()
        if url.startswith(base_url): return url[len(base_url):].lstrip("/").split("?", 1)[0]
        decoded = urllib.parse.unquote(url)
        if decoded.startswith(base_url): return decoded[len(base_url):].lstrip("/").split("?", 1)[0]
        return None


    tmp_dir = tempfile.mkdtemp(prefix=f"deploy_{website_id[:8]}_")
    try:
        # 1. Resolve page list
        saved_layout = await get_website_layout(website_id)
        pages_to_build = ["home", "about", "services", "portfolio", "contact"]
        if saved_layout:
            pages_to_build = list(dict.fromkeys(pages_to_build + saved_layout))

        # 2. Collect HTML
        page_htmls = {}
        for page in pages_to_build:
            filename   = f"{page}.html"
            object_key = f"websites/{website_id}/{filename}"
            try:
                html_bytes = await asyncio.to_thread(fetch_media_from_r2, object_key)
                if not html_bytes: continue
                out_name = "index.html" if page == "home" else filename
                page_htmls[page] = (out_name, html_bytes.decode("utf-8"))
            except Exception:
                pass

        if not page_htmls:
            raise Exception("No website pages found for deployment.")

        missing_pages = [f"{p}.html" for p in pages_to_build if p not in page_htmls]

        # 3. Discover Images
        IMG_SRC_PATTERN = re.compile(r'(?:src=["\']|url\(["\']?)(' + re.escape(base_url) + r'/[^"\')\s>]+)', re.IGNORECASE)
        all_r2_urls = set()
        for _, (_, html) in page_htmls.items():
            matches = IMG_SRC_PATTERN.findall(html)
            all_r2_urls.update(matches)

        # 4. Fetch & Compress Images
        asset_map, asset_bytes_map = {}, {}
        for r2_url in all_r2_urls:
            object_key = url_to_object_key(r2_url)
            if not object_key: continue
            original_filename = object_key.split('/')[-1].split('?')[0]
            lower_key = object_key.lower()
            
            try:
                raw = await asyncio.to_thread(fetch_media_from_r2, object_key)
                if not raw: continue
                if any(lower_key.endswith(ext) for ext in (".svg", ".woff", ".woff2", ".ttf", ".eot", ".otf", ".mp4", ".webm")):
                    local_name = f"assets/{original_filename}"
                    asset_map[r2_url] = local_name
                    asset_bytes_map[local_name] = raw
                else:
                    local_name = f"assets/{original_filename}"
                    if not local_name.lower().endswith('.webp'):
                        local_name = local_name.rsplit('.', 1)[0] + ".webp"
                    compressed = await asyncio.to_thread(compress_image, raw)
                    asset_map[r2_url] = local_name
                    asset_bytes_map[local_name] = compressed
            except Exception:
                pass

        # 5. Build into Temp Folder
        assets_dir = os.path.join(tmp_dir, "assets")
        os.makedirs(assets_dir, exist_ok=True)
        
        for local_path, data in asset_bytes_map.items():
            with open(os.path.join(tmp_dir, local_path), 'wb') as f:
                f.write(data)

        for page, (out_name, html) in page_htmls.items():
            html = clean_editor_artifacts(html)
            for missing in missing_pages:
                html = re.sub(rf'<a[^>]*href=["\']{missing}(#[^"\']*)?["\'][^>]*>.*?</a>', '', html, flags=re.IGNORECASE)

            for r2_url, local_rel in asset_map.items():
                html = html.replace(r2_url, local_rel)

            html = re.sub(fr'/preview/{re.escape(website_id)}/', '', html)
            html = re.sub(
                r'href=(["\'])home\.html(#[^"\']*)?(["\'])',
                lambda m: f'href={m.group(1)}index.html{m.group(2) or ""}{m.group(1)}',
                html, flags=re.IGNORECASE
            )
            with open(os.path.join(tmp_dir, out_name), 'w', encoding='utf-8') as f:
                f.write(html)

        project_name = os.getenv("VERCEL_PROJECT_NAME", "website-builder-portal")
        vercel_json = { "name": project_name }
        with open(os.path.join(tmp_dir, 'vercel.json'), 'w') as f:
            json.dump(vercel_json, f)

        # 6. Run Vercel Deploy
        # We use --json to get a clean machine-readable URL
        scope = os.getenv("VERCEL_SCOPE")
        cmd = f'npx --yes vercel deploy --prod --yes --token {vercel_token} --json'
        if scope:
            cmd += f' --scope {scope}'

        process = await asyncio.create_subprocess_shell(
            cmd,
            cwd=tmp_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ,   # Leak Fix #5: pass the live env dict directly; the OS
                               # creates its own copy for the child process, so we
                               # avoid allocating a large dict copy per deployment call.
        )
        # Leak Fix #5: timeout communicate() so that if the Vercel CLI hangs the
        # PIPE buffer cannot grow unboundedly and the subprocess is not left as a
        # zombie. 1200s = 20 minutes (matches the worker's 25-minute job timeout).
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=1200
            )
        except asyncio.TimeoutError:
            try:
                process.kill()
                await process.wait()
            except Exception:
                pass
            raise Exception("Vercel CLI timed out after 20 minutes")
        out_str = stdout_bytes.decode('utf-8')
        err_str = stderr_bytes.decode('utf-8')

        if process.returncode != 0:
            raise Exception(f"Vercel CLI failed: {err_str}")

            
        # Parse JSON output to get the deployment URL
        try:
            # Find the JSON part in the output
            start = out_str.find('{')
            end = out_str.rfind('}') + 1
            data = json.loads(out_str[start:end])
            
            # Get the unique deployment URL
            final_url = data.get("url")
            if final_url:
                if not final_url.startswith("http"):
                    final_url = f"https://{final_url}"
                return final_url
        except Exception:
            pass

        # Fallback to regex if JSON parsing fails
        deploy_pattern = re.compile(r'https://[a-zA-Z0-9\-\._]+\.vercel\.app', re.IGNORECASE)
        urls = deploy_pattern.findall(out_str + err_str)
        
        if urls:
            return urls[-1]
        else:
            raise Exception("Site deployed but URL could not be captured.")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
