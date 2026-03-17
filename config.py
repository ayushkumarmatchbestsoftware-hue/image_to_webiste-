import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

    # File upload settings
    UPLOAD_FOLDER = "static/uploads"
    GENERATED_FOLDER = "static/generated"

    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max upload
    MAX_IMAGES = 5

    # Allowed image formats
    ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}