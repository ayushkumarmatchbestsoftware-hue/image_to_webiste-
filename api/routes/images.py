"""
Image AI Tools: Background Removal & User-Prompt Image Generation API.

Endpoints:
- POST /api/images/remove-bg : Remove background, output transparent PNG or color ground.
- POST /api/images/generate  : Real high-resolution image generation from user prompts.
- GET  /api/images/status    : Provider capabilities, models, and readiness.
"""
import io
import logging
import uuid
import os
from typing import Optional
from fastapi import APIRouter, File, UploadFile, Form, HTTPException, Query, Response
from fastapi.responses import JSONResponse, Response

from core import bgremover
from core import image_generator
from config import Config

logger = logging.getLogger("images_api")

router = APIRouter(prefix="/api/images", tags=["Image AI Tools"])


@router.get("/status")
async def get_image_tools_status():
    """Diagnostic status for background removal and prompt-based image generation."""
    from core.llm import PROVIDER, api_key
    provider_name, key = image_generator._get_provider_and_key()
    bg_info = bgremover.info()

    return {
        "status": "ok",
        "bg_remover": {
            "enabled": bg_info.get("enabled", True),
            "usable": bg_info.get("usable", True),
            "models": bg_info.get("models", []),
            "local_backend": bgremover.provider() or "local-edge-matting"
        },
        "image_generator": {
            "provider": provider_name,
            "key_configured": bool(key),
            "supported_aspect_ratios": list(image_generator.ASPECT_RATIOS.keys()),
            "supported_styles": list(image_generator.STYLES.keys())
        }
    }


@router.post("/remove-bg")
async def remove_image_background(
    file: UploadFile = File(..., description="Product photograph to remove background from"),
    transparent: bool = Form(True, description="Return transparent PNG if true"),
    bg_color: Optional[str] = Form(None, description="Optional hex color to place product on (e.g. #f4f2ef)"),
    return_json: bool = Form(False, description="If true, returns JSON with base64 and dimensions")
):
    """
    Remove background from a product photograph.
    Returns clean transparent PNG or color-grounded JPEG.
    """
    try:
        photo_bytes = await file.read()
        if not photo_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        out_bytes, mime_type = bgremover.remove_background(
            photo_bytes=photo_bytes,
            transparent=transparent,
            bg_color=bg_color
        )

        if return_json:
            import base64
            from PIL import Image
            img = Image.open(io.BytesIO(out_bytes))
            w, h = img.size
            return JSONResponse({
                "status": "success",
                "mime_type": mime_type,
                "width": w,
                "height": h,
                "size_bytes": len(out_bytes),
                "data_base64": f"data:{mime_type};base64,{base64.b64encode(out_bytes).decode('utf-8')}"
            })

        return Response(content=out_bytes, media_type=mime_type)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Background removal failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Background removal failed: {str(e)}")


@router.post("/generate")
async def generate_prompt_image(
    prompt: str = Form(..., description="Prompt describing the product or scene"),
    aspect_ratio: str = Form("hero", description="Aspect ratio: hero (16:9), card (1:1), portrait (3:4)"),
    style: str = Form("photorealistic", description="Style: photorealistic, lifestyle, minimalist, artisan, tech"),
    category: Optional[str] = Form(None, description="Product category context"),
    return_json: bool = Form(False, description="If true, returns JSON with base64 data and metadata")
):
    """
    Generate a high-quality, website-ready image from a user prompt.
    """
    try:
        if not prompt or not prompt.strip():
            raise HTTPException(status_code=400, detail="Prompt is required.")

        img_bytes, mime_type, width, height = image_generator.generate_image(
            prompt=prompt.strip(),
            aspect_ratio=aspect_ratio,
            style=style,
            category=category,
            allow_fallback=True
        )

        if return_json:
            import base64
            return JSONResponse({
                "status": "success",
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "style": style,
                "width": width,
                "height": height,
                "mime_type": mime_type,
                "size_bytes": len(img_bytes),
                "data_base64": f"data:{mime_type};base64,{base64.b64encode(img_bytes).decode('utf-8')}"
            })

        return Response(content=img_bytes, media_type=mime_type)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Image generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Image generation failed: {str(e)}")
