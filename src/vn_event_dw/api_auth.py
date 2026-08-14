from __future__ import annotations

import os
import secrets

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

API_KEY_ENV_VAR = "VN_EVENT_DW_API_KEY"
API_KEY_HEADER = "X-API-Key"


def api_key_from_env() -> str:
    return os.getenv(API_KEY_ENV_VAR, "").strip()


def _request_api_key(request: Request) -> str:
    header_key = request.headers.get(API_KEY_HEADER, "").strip()
    if header_key:
        return header_key

    auth_header = request.headers.get("Authorization", "").strip()
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return ""


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Protect public read APIs when VN_EVENT_DW_API_KEY is configured."""

    async def dispatch(self, request: Request, call_next) -> Response:
        expected_key = api_key_from_env()
        if not expected_key or not request.url.path.startswith("/api/"):
            return await call_next(request)

        provided_key = _request_api_key(request)
        if not provided_key:
            return JSONResponse(
                status_code=401,
                content={"detail": f"Missing API key. Send it in the {API_KEY_HEADER} header."},
                headers={"WWW-Authenticate": "ApiKey"},
            )
        if not secrets.compare_digest(provided_key, expected_key):
            return JSONResponse(status_code=403, content={"detail": "Invalid API key."})

        return await call_next(request)
