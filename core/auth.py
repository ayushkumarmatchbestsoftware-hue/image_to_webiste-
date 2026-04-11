import uuid
import logging
import os
from typing import Optional
from dataclasses import dataclass
from jose import jwt, JWTError
from config import Config as settings

# ===================================
# Logger
# ===================================

logger = logging.getLogger(__name__)


# ===================================
# JWT Configuration
# ===================================

SECRET_KEY: str = settings.JWT_SECRET
ALGORITHM: str = settings.JWT_ALGORITHM

if not SECRET_KEY:
    raise RuntimeError("JWT_SECRET is not configured")


# ===================================
# Auth User Model
# ===================================

@dataclass
class AuthUser:
    user_id: uuid.UUID
    device_id: Optional[str] = None
    session_id: Optional[str] = None
    platform: Optional[str] = None


# ===================================
# Token Utilities
# ===================================

def _extract_token_from_header(auth_header: Optional[str]) -> str:
    """
    Extract Bearer token from Authorization header.
    """
    if not auth_header:
        raise ValueError("Authorization header missing")

    parts = auth_header.split()

    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise ValueError("Invalid Authorization header format")

    token = parts[1].strip()

    if not token:
        raise ValueError("Token missing in Authorization header")

    return token


def _decode_jwt_token(token: str) -> AuthUser:
    """
    Decode and validate JWT token.
    """
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            options={"verify_aud": False},
        )

    except JWTError as e:
        logger.warning(f"JWT decode failed: {str(e)}")
        raise ValueError("Invalid or expired token")

    # -----------------------------------
    # Extract User ID
    # -----------------------------------
    user_id = (
        payload.get("user_id")
        or payload.get("sub")
        or payload.get("id")
    )

    if not user_id:
        raise ValueError("Token missing user identifier")

    try:
        user_uuid = uuid.UUID(str(user_id))
    except (ValueError, TypeError):
        raise ValueError("Invalid user_id format in token")

    # -----------------------------------
    # Optional fields
    # -----------------------------------
    return AuthUser(
        user_id=user_uuid,
        device_id=payload.get("deviceId"),
        session_id=payload.get("sessionId"),
        platform=payload.get("platform"),
    )


# ===================================
# Public Auth Helper (Flask)
# ===================================

def get_current_user_flask():
    """
    Flask helper for retrieving authenticated user from current request.
    Supports both Authorization header and 'token' query parameter.
    """
    from flask import request
    
    # 1. Try Authorization header (Standard API calls)
    auth_header = request.headers.get("Authorization")
    if auth_header:
        try:
            token = _extract_token_from_header(auth_header)
            return _decode_jwt_token(token)
        except ValueError:
            pass  # Fallback to query param

    # 2. Try 'token' query parameter (Direct browser redirects/new tabs)
    token = request.args.get("token")
    if token:
        try:
            return _decode_jwt_token(token)
        except ValueError:
            pass

    return None

def get_request_user_id():
    from flask import g
    return getattr(g, "user_id", None)