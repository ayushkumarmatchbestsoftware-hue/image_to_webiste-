import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    # Authentication
    JWT_SECRET = os.getenv("JWT_SECRET")
    JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")

    # File upload settings
    UPLOAD_FOLDER = "static/uploads"
    GENERATED_FOLDER = "static/generated"

    MAX_CONTENT_LENGTH = 30 * 1024 * 1024  # 16MB max upload
    MAX_IMAGES = 5

    # Allowed image formats
    ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "heic", "heif"}