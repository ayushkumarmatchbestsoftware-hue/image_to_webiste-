import os
import uuid
import json
import re
import asyncio
import zipfile
import random
import logging
import traceback
import sys
import io
import shutil
import tempfile
from datetime import datetime, timedelta
from typing import Optional, List, Union, Dict
import aiofiles

# Fix for "Event loop is closed" error during asyncio subprocess calls on Windows.
if os.name == 'nt':
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except AttributeError:
        pass

from fastapi import FastAPI, Request, Depends, HTTPException, Form, File, UploadFile, Query, Cookie, BackgroundTasks, Response, Header
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from werkzeug.utils import secure_filename
from google import genai
from PIL import Image
from bson import ObjectId
from config import Config
import uuid as pyuuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import insert, update, select, func
from core.db import get_session_factory, init_db
from core.mongo import insert_website_data, get_website_layout, update_website_layout, insert_chat_message, get_websites_collection, update_website_final_url
from core.r2 import upload_media_to_r2, R2_PUBLIC_URL, fetch_media_from_r2, r2_client, R2_BUCKET_NAME
from services.credit_query_service import get_user_real_credit
from handlers.credit_handler import website_credits_debits
from model.website_schema import WebsiteInfo, ChatMessage
from model.img_info_schema import ImageInfo
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURATION ---
WEBSITE_AI_CREDIT_COST = os.getenv("WEBSITE_AI_CREDIT_COST", "1")
ENABLE_CREDIT_SYSTEM = os.getenv("ENABLE_CREDIT_SYSTEM", "True") == "True"
ENABLE_CHAT_EDIT = False  # Disabled as requested
MAX_IMAGES = Config.MAX_IMAGES

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger("fastapi")

# --- APP INITIALIZATION ---
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _DB_READY
    try:
        await init_db()
        _DB_READY = True
        logger.info(">>> [BOOT] Database initialized")
    except Exception as e:
        logger.error(f">>> [BOOT ERROR] Database init failed: {e}")
    yield

app = FastAPI(title="Pomeli Website Builder API", lifespan=lifespan)

# CORS Setup
raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")
allowed_origins = [orig.strip() for orig in raw_origins.split(",") if orig.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static & Templates
os.makedirs(Config.GENERATED_FOLDER, exist_ok=True)
os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Global state
WEBSITE_CONTEXTS: dict = {}
_DB_READY = False

# --- AUTHENTICATION DEPENDENCY ---
async def get_current_user_id(
    request: Request,
    token: Optional[str] = Query(None),
    auth_token: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None)
) -> Optional[str]:
    if Config.DEV_MODE:
        return "00000000-0000-0000-0000-000000000001"
    
    from core.auth import _decode_jwt_token, _extract_token_from_header
    
    final_token = None
    if authorization:
        try:
            final_token = _extract_token_from_header(authorization)
        except: pass
    
    if not final_token:
        final_token = auth_token or token
        
    if not final_token:
        return None
    
    try:
        user = _decode_jwt_token(final_token)
        return str(user.user_id)
    except Exception as e:
        logger.error(f"Auth failed: {e}")
        return None

async def require_auth(user_id: str = Depends(get_current_user_id)):
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user_id

# --- CREDITS DEPENDENCY ---
async def check_credits(user_id: str = Depends(require_auth)):
    if not ENABLE_CREDIT_SYSTEM or Config.DEV_MODE:
        return
    
    amount = int(WEBSITE_AI_CREDIT_COST)
    result = await get_user_real_credit(user_id)
    balance = result["data"]["balance"] if result.get("ok") else 0
    
    if balance < amount:
        logger.warning(f">>> [CREDIT BLOCK] User {user_id} rejected. Has {balance}, needs {amount}.")
        raise HTTPException(
            status_code=402, 
            detail={
                "error": "INSUFFICIENT_CREDITS",
                "required": amount,
                "available": balance
            }
        )

# --- AI CLIENT ---
try:
    _api_key = Config.GEMINI_API_KEY
    genai_client = genai.Client(api_key=_api_key) if _api_key else None
except Exception as e:
    logger.error(f"GenAI Init failed: {e}")
    genai_client = None

# ---------------------------------------------------------------------------
# CONSTANTS & UTILITIES
# ---------------------------------------------------------------------------
# --- DESIGN UTILS ---
from core.constants import NICHE_DESIGN, LAYOUT_POOLS, PALETTE_MAP, INDUSTRY_TEMPLATES, system_prompt_text
from core.utils import (
    get_niche_key_logic, 
    get_fallback_tokens_logic, 
    get_layout_blueprint_logic, 
    validate_and_fix_theme,
    generate_website_content_logic,
    clean_editor_artifacts,
    perform_chat_edit_logic
)

def get_fallback_tokens(prompt: str):
    return get_fallback_tokens_logic(prompt, NICHE_DESIGN)

def get_layout_blueprint(prompt: str):
    return get_layout_blueprint_logic(prompt, LAYOUT_POOLS)

async def generate_website_content(prompt, image_paths=None, image_count=0, industry=None):
    return await generate_website_content_logic(
        genai_client, prompt, system_prompt_text, get_fallback_tokens, get_layout_blueprint, 
        INDUSTRY_TEMPLATES, validate_and_fix_theme, image_paths=image_paths, 
        image_count=image_count, industry=industry
    )

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_EXTENSIONS

# ---------------------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "Website AI API"}

@app.get("/")
async def index():
    return {"status": "online", "service": "Website AI API", "message": "Backend is running correctly."}

@app.post("/generate")
async def generate_website(
    request: Request,
    background_tasks: BackgroundTasks,
    prompt: str = Form(...),
    industry: str = Form(""),
    pages: str = Form(""),
    palette: str = Form("auto"),
    logo: Optional[UploadFile] = File(None),
    images: List[UploadFile] = File([]),
    user_id: str = Depends(require_auth),
    _ = Depends(check_credits)
):
    try:
        website_id = str(uuid.uuid4())
        website_folder = os.path.join(Config.GENERATED_FOLDER, website_id)
        os.makedirs(website_folder, exist_ok=True)

        db_image_records = []
        logo_web_path = None

        if logo and logo.filename:
            logo_bytes = await logo.read()
            logo_web_path = await asyncio.to_thread(
                upload_media_to_r2, logo_bytes, logo.content_type,
                folder=f"websites/{website_id}/assets"
            )
            # Add to records...
        
        image_urls = []
        image_paths = []
        for file in images:
            if file and file.filename:
                file_bytes = await file.read()
                unique_filename = f"{uuid.uuid4()}_{secure_filename(file.filename)}"
                filepath = os.path.join(Config.UPLOAD_FOLDER, unique_filename)
                async with aiofiles.open(filepath, "wb") as f:
                    await f.write(file_bytes)
                
                web_path = await asyncio.to_thread(
                    upload_media_to_r2, file_bytes, file.content_type,
                    folder=f"websites/{website_id}/assets"
                )
                image_urls.append(web_path)
                image_paths.append(filepath)

        from core.redis import enqueue_website_ai_job
        print(f"[API IO] -> /generate | user={user_id} | industry={industry} | prompt='{prompt[:50]}...'")
        job_id = await enqueue_website_ai_job(
            website_id=website_id, user_id=user_id, prompt=prompt,
            image_urls=image_urls, image_paths=image_paths, logo_url=logo_web_path,
            user_pages=pages, user_palette=palette, user_industry=industry,
            db_image_records=db_image_records
        )

        print(f"[API IO] <- /generate | QUEUED | job_id={job_id} | website_id={website_id}")
        return {"success": True, "job_id": job_id, "website_id": website_id, "status": "queued"}
    except Exception as e:
        logger.error(f"Generate failed: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/job-status/{job_id}")
async def job_status(job_id: str, user_id: str = Depends(require_auth)):
    from core.redis import get_job_status, mark_job_failed
    data = await get_job_status(job_id)
    if not data:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Timeout check logic...
    return data

@app.get("/history")
async def history(user_id: str = Depends(require_auth)):
    print(f"[API IO] -> /history | user={user_id} | filter_days=7")
    cutoff = datetime.utcnow() - timedelta(days=7)
    collection = get_websites_collection()
    query = {"user_id": user_id, "_id": {"$gte": ObjectId.from_datetime(cutoff)}}
    cursor = collection.find(query).sort("_id", -1)
    websites = await cursor.to_list(length=None)
    print(f"[API IO] <- /history | FOUND={len(websites)} items")
    
    items = []
    for w in websites:
        wid = str(w.get("website_id", w.get("_id")))
        items.append({
            "website_id": wid,
            "site_name": w.get("site_name"),
            "final_url": w.get("final_url"),
            "preview_url": f"/preview/{wid}/home.html",
            "status": w.get("status"),
            "created_at": w.get("_id").generation_time.isoformat()
        })
    return {"success": True, "items": items}

@app.post("/deploy")
async def deploy_to_vercel(request: Request, user_id: str = Depends(require_auth)):
    try:
        # Check if it's a JSON body (expected) or path-based params
        try:
            data = await request.json()
            website_id = data.get("website_id")
        except:
            website_id = None

        if not website_id:
            return JSONResponse(status_code=400, content={"error": "Missing website_id in JSON body"})
        
        from core.redis import enqueue_deployment_job
        print(f"[API IO] -> /deploy | user={user_id} | website_id={website_id}")
        job_id = await enqueue_deployment_job(website_id=website_id, user_id=user_id)
        print(f"[API IO] <- /deploy | QUEUED | job_id={job_id}")
        return {"success": True, "job_id": job_id, "status": "queued"}
    except Exception as e:
        logger.error(f"Deploy trigger failed: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/download/{website_id}")
async def download_website(website_id: str, user_id: str = Depends(require_auth)):
    try:
        import zipfile
        import io
        from core.r2 import fetch_media_from_r2, list_objects_in_folder
        
        # 1. List all files for this website in R2
        prefix = f"websites/{website_id}/"
        files = await asyncio.to_thread(list_objects_in_folder, prefix)
        
        if not files:
            raise HTTPException(status_code=404, detail="No files found for this website")
        
        # 2. Create ZIP in memory
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for f in files:
                obj_key = f['Key']
                rel_path = obj_key[len(prefix):]
                
                # Exclude internal files like backups
                if not rel_path or rel_path == "home_backup.html":
                    continue
                
                content = await asyncio.to_thread(fetch_media_from_r2, obj_key)
                if content:
                    # Clean HTML files before zipping for a "clean", local-ready export
                    if rel_path.endswith(".html"):
                        try:
                            html_str = content.decode('utf-8')
                            # 1. Remove editor-only markup
                            html_str = clean_editor_artifacts(html_str)
                            # 2. Strip the preview proxy prefix from all links
                            # Converts: href="/preview/{id}/about.html" -> href="about.html"
                            html_str = re.sub(
                                r'href=["\']\/preview\/[a-f0-9\-]+\/([\w\-.]+)(["\'])',
                                r'href="\1\2',
                                html_str
                            )
                            content = html_str.encode('utf-8')
                        except: pass
                    zip_file.writestr(rel_path, content)
        
        zip_buffer.seek(0)
        return Response(
            content=zip_buffer.getvalue(),
            media_type="application/x-zip-compressed",
            headers={
                "Content-Disposition": f"attachment; filename=website_export_{website_id[:8]}.zip"
            }
        )
    except Exception as e:
        logger.error(f"Download failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/upload-image")
async def upload_image(
    request: Request,
    image: UploadFile = File(...),
    website_id: str = Form(...),
    old_url: str = Form(""),
    user_id: str = Depends(require_auth)
):
    try:
        from core.r2 import upload_media_to_r2, fetch_media_from_r2, list_objects_in_folder
        
        file_bytes = await image.read()
        unique_filename = f"{uuid.uuid4()}_{secure_filename(image.filename)}"
        
        # 1. Upload new image to R2
        web_path = await asyncio.to_thread(
            upload_media_to_r2, file_bytes, image.content_type,
            folder=f"websites/{website_id}/assets", filename=unique_filename
        )
        
        # 2. GLOBAL REPLACE: If we have an old_url, update all HTML files to point to the new one
        if old_url and old_url.strip():
            # Standardize URL for matching: Strip query params (cache busters) if present
            search_url = old_url.strip().split('?')[0]
            
            # List all HTML files in the website folder
            prefix = f"websites/{website_id}/"
            files = await asyncio.to_thread(list_objects_in_folder, prefix)
            
            for f in files:
                key = f['Key']
                if key.endswith(".html"):
                    try:
                        html_bytes = await asyncio.to_thread(fetch_media_from_r2, key)
                        if html_bytes:
                            html_str = html_bytes.decode('utf-8')
                            if search_url in html_str:
                                # Replace and save back to R2
                                new_html = html_str.replace(search_url, web_path)
                                await asyncio.to_thread(
                                    upload_media_to_r2, new_html.encode('utf-8'), "text/html",
                                    folder=f"websites/{website_id}", filename=key[len(prefix):]
                                )
                                logger.info(f"Global Replace: Updated {key} with new image URL")
                    except Exception as e:
                        logger.error(f"Global Replace failed for {key}: {e}")

        return {"success": True, "url": web_path}
    except Exception as e:
        logger.error(f"Image upload failed: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/get-latest-job/{website_id}")
async def get_latest_job(website_id: str, user_id: str = Depends(require_auth)):
    from core.redis import get_job_id_for_website, get_job_status
    job_id = await get_job_id_for_website(website_id)
    if not job_id:
        return {"success": False, "error": "No job found"}
    
    status_data = await get_job_status(job_id)
    return {"success": True, "job_id": job_id, "data": status_data}

@app.get("/preview/{website_id}/{filename:path}")
async def preview_proxy(website_id: str, filename: str = "home.html"):
    try:
        clean_filename = (filename or 'home.html').strip('/')
        if not clean_filename: clean_filename = 'home.html'
        
        object_key = f"websites/{website_id}/{clean_filename}"
        html_bytes = None

        search_keys = [object_key]
        if clean_filename in ('home.html', 'index.html'):
            alt = 'index.html' if clean_filename == 'home.html' else 'home.html'
            search_keys.append(f"websites/{website_id}/{alt}")

        for key in search_keys:
            try:
                html_bytes = await asyncio.to_thread(fetch_media_from_r2, key)
                if html_bytes: break
            except: pass

        if not html_bytes:
            # Check Redis for a failure message
            from core.redis import get_job_id_for_website, get_job_status
            job_id = await get_job_id_for_website(website_id)
            if job_id:
                status_data = await get_job_status(job_id)
                if status_data and status_data.get('status') == 'failed':
                    return HTMLResponse(
                        content=f"Generation Failed: {status_data.get('error', 'Unknown Error')}<br>Please try generating again.",
                        status_code=200
                    )
            
            return HTMLResponse(content="Still generating... (Try refreshing in a few seconds)", status_code=200)

        html = html_bytes.decode("utf-8")
        # Strip editor elements
        html = clean_editor_artifacts(html)
        
        # --- ROBUST LINK REWRITING ---
        # Catch variations like: "about", "about.html", "/about.html", "./about.html"
        BASE = f"/preview/{website_id}"
        NAMED_PAGES = ["home", "about", "services", "portfolio", "contact"]
        
        for p in NAMED_PAGES:
            # 1. Matches: href="about.html" or href='about.html'
            html = re.sub(rf'href=(["\'])(?:\./|/)?{p}(?:\.html)?(#[^"\']*)?\1', rf'href=\1{BASE}/{p}.html\2\1', html, flags=re.I)
            # 2. Matches: href="about" (no extension)
            # (Handled by the improved regex above)

        return HTMLResponse(content=html)
    except Exception as e:
        logger.error(f"Preview failed: {e}")
        return HTMLResponse(content=f"Preview Error: {e}", status_code=500)

@app.post("/chat-edit")
async def chat_edit(request: Request, user_id: str = Depends(require_auth)):
    try:
        data = await request.json()
        instruction = data.get('instruction')
        html = data.get('html')
        page_name = data.get('page_name') or 'home.html'
        
        if not instruction or not html:
            raise HTTPException(status_code=400, detail="Missing data")
            
        result = await perform_chat_edit_logic(genai_client, instruction, html, page_name)
        return result
    except Exception as e:
        logger.error(f"Chat edit failed: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/save")
async def save_html(request: Request, user_id: str = Depends(require_auth)):
    try:
        data = await request.json()
        website_id = data.get('website_id')
        html = data.get('html')
        page_name = os.path.basename(data.get('page_name') or 'home.html')
        
        if not website_id or not html:
            raise HTTPException(status_code=400, detail="Missing data")

        print(f"[API IO] -> /save | website_id={website_id} | page={page_name} | html_len={len(html)}")
        # Keep artifacts on save so they remain editable later
        # html = clean_editor_artifacts(html)
        await asyncio.to_thread(
            upload_media_to_r2, html.encode('utf-8'), "text/html",
            folder=f"websites/{website_id}", filename=page_name
        )
        print(f"[API IO] <- /save | SUCCESS | path=websites/{website_id}/{page_name}")
        return {"success": True}
    except Exception as e:
        logger.error(f"Save failed: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/editor/{website_id}")
async def editor_page(request: Request, website_id: str, token: Optional[str] = Query(None)):
    if token:
        response = RedirectResponse(url=f"/editor/{website_id}")
        response.set_cookie(
            'auth_token', token, httponly=True, samesite='Lax', secure=False, path='/'
        )
        return response
    return templates.TemplateResponse("editor.html", {
        "request": request, 
        "website_id": website_id, 
        "chat_enabled": ENABLE_CHAT_EDIT,
        "is_editor": True
    })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
