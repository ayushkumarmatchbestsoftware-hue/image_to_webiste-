import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # OpenAI is the active provider for every model call (core/llm.py).
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL_CONTENT = os.getenv("OPENAI_MODEL_CONTENT", "gpt-4.1")
    OPENAI_MODEL_FAST = os.getenv("OPENAI_MODEL_FAST", "gpt-4.1-mini")
    # Retained so existing imports keep resolving; no longer used for calls.
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
    DEBUG = os.getenv("DEBUG", "true").lower() == "true" if ENVIRONMENT == "development" else False
    
    # Authentication
    JWT_SECRET = os.getenv("JWT_SECRET")
    JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
    
    # DEV_MODE: Hard-disabled in production for security, otherwise read from env.
    _raw_dev_mode = os.getenv("DEV_MODE", "false").lower() == "true"
    DEV_MODE = _raw_dev_mode if ENVIRONMENT == "development" else False

    # File upload settings
    UPLOAD_FOLDER = "static/uploads"
    GENERATED_FOLDER = "static/generated"

    MAX_CONTENT_LENGTH = 30 * 1024 * 1024  # 16MB max upload
    MAX_IMAGES = 5

    # Allowed image formats
    ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "heic", "heif"}