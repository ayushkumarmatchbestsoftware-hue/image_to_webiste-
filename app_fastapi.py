import os
import uuid
import json
import re
import asyncio
import random
import logging
import traceback
import sys
from datetime import datetime, timedelta
from typing import Optional, List, Union
from dotenv import load_dotenv

load_dotenv()

from core.telemetry import (
    configure_logging,
    instrument_fastapi_app,
    setup_telemetry,
    shutdown_telemetry,
)

configure_logging()
setup_telemetry()

# Fix for "Event loop is closed" error during asyncio subprocess calls on Windows.
if os.name == 'nt':
    import asyncio
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except AttributeError:
        pass

from fastapi import FastAPI, Request, Depends, HTTPException, Form, File, UploadFile, Query, Cookie, BackgroundTasks, Response
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
import aiofiles
from google import genai
from PIL import Image
from bson import ObjectId
from config import Config
import uuid as pyuuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import insert, update, select, func
from core.db import get_session_factory, init_db
from core.mongo import insert_website_data, get_website_layout, update_website_layout, insert_chat_message, get_websites_collection, update_website_final_url
from core.r2 import upload_media_to_r2, R2_PUBLIC_URL, fetch_media_from_r2
from services.credit_query_service import get_user_real_credit
from model.website_schema import WebsiteInfo, ChatMessage
from model.img_info_schema import ImageInfo
import io
import shutil
import tempfile

WEBSITE_AI_CREDIT_COST = os.getenv("WEBSITE_AI_CREDIT_COST", "1")
ENABLE_CREDIT_SYSTEM = os.getenv("ENABLE_CREDIT_SYSTEM", "True") == "True"
ENABLE_CHAT_EDIT = os.getenv("ENABLE_CHAT_EDIT", "False").lower() == "true"

# Setup FastAPI
app = FastAPI(title="Pomeli Website Builder API")
instrument_fastapi_app(app)

# Templates
templates = Jinja2Templates(directory="templates")

# Static Files
os.makedirs(Config.GENERATED_FOLDER, exist_ok=True)
os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
app.mount("/generated", StaticFiles(directory=Config.GENERATED_FOLDER), name="generated")

# Logging
logger = logging.getLogger("fastapi")

# CORS
TRACE_CONTEXT_HEADERS = ["traceparent", "tracestate", "baggage"]
raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")
allowed_origins = [orig.strip() for orig in raw_origins.split(",") if orig.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", *TRACE_CONTEXT_HEADERS],
)

# Global variables
WEBSITE_CONTEXTS: dict = {}
_DB_READY = False

@app.on_event("startup")
async def startup_event():
    global _DB_READY
    try:
        await init_db()
        _DB_READY = True
        logger.info(">>> [BOOT] Database initialized")
    except Exception as e:
        logger.error(f">>> [BOOT ERROR] Database init failed: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    shutdown_telemetry()

# --- Authentication Dependency ---
async def get_current_user(
    request: Request,
    token: Optional[str] = Query(None),
    auth_token: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None)
):
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
        raise HTTPException(status_code=401, detail="Authentication required")
    
    try:
        user = _decode_jwt_token(final_token)
        return str(user.user_id)
    except Exception as e:
        logger.error(f"Auth failed: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired token")

# --- Credits Dependency ---
async def check_credits(user_id: str = Depends(get_current_user)):
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

# --- AI Client ---
try:
    _api_key = Config.GEMINI_API_KEY
    genai_client = genai.Client(api_key=_api_key) if _api_key else None
except Exception as e:
    logger.error(f"GenAI Init failed: {e}")
    genai_client = None

# --- Constants & Helpers ---
MAX_IMAGES = Config.MAX_IMAGES
NICHE_DESIGN = { ... } # Copy from app.py
LAYOUT_POOLS = { ... } # Copy from app.py
PALETTE_MAP = { ... } # Copy from app.py

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}

# [ROUTES WILL BE IMPLEMENTED IN NEXT STEP]
