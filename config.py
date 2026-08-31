"""
Settings that are not the model's and not the store's.

The provider and its models resolve in core/llm.py from LLM_PROVIDER and
LLM_API_KEY, not here. What remains is what the upload path enforces.
"""
import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    # Where an upload lands and where a rendered site is written before it
    # reaches the store. api/server.py creates both at boot.
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "static/uploads")
    GENERATED_FOLDER = os.getenv("GENERATED_FOLDER", "static/generated")

    MAX_CONTENT_LENGTH = 30 * 1024 * 1024
    MAX_IMAGES = 5

    # heic and heif are here because that is what phones shoot by default.
    # core/__init__.py registers the decoder for them; without it Pillow accepts
    # the extension and then cannot open the file.
    ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "heic", "heif"}
