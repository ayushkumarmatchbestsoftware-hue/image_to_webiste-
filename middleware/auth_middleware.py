import logging
from typing import List

from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from core.auth import AuthUser


logger = logging.getLogger(__name__)


class AuthContextMiddleware(BaseHTTPMiddleware):

    # Routes that don't require authentication
    EXCLUDED_PATHS: List[str] = [
        "/health",
    ]

    async def dispatch(self, request: Request, call_next):

        path = request.url.path

        # Skip authentication for excluded routes
        if any(path.startswith(p) for p in self.EXCLUDED_PATHS):
            return await call_next(request)

        try:
            user: AuthUser = get_current_user(request)

            # Inject user context into request.state
            request.state.user = user
            request.state.user_id = user.user_id
            request.state.device_id = user.device_id
            request.state.session_id = user.session_id
            request.state.platform = user.platform

        except HTTPException as e:
            logger.warning(
                f"Auth failed | path={path} | detail={e.detail}"
            )

            return JSONResponse(
                status_code=e.status_code,
                content={"detail": e.detail},
            )

        except Exception:
            logger.exception("Unexpected auth middleware error")

            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Authentication failed"},
            )

        # Continue request processing
        response = await call_next(request)

        return response