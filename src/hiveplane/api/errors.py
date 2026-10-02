"""API v2 request-id middleware and structured error envelope (M56-01)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Correlate every request/response with an ``X-Request-ID``."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


def _safe_detail(detail: Any) -> Any:
    """Return a JSON-serializable detail, stringifying exotic values."""
    if detail is None or isinstance(detail, (str, int, float, bool, list, dict)):
        return detail
    return str(detail)


def error_envelope(
    request: Request,
    status_code: int,
    detail: Any,
    message: str | None = None,
    details: Any = None,
) -> JSONResponse:
    """Build the v2 error envelope, keeping the default ``detail`` shape."""
    request_id = getattr(request.state, "request_id", None)
    resolved_message = message if message is not None else str(detail)
    content = jsonable_encoder(
        {
            "detail": _safe_detail(detail),
            "error": {
                "code": status_code,
                "message": resolved_message,
                "details": details,
                "request_id": request_id,
            },
        }
    )
    return JSONResponse(
        status_code=status_code,
        content=content,
        headers={"X-Request-ID": request_id} if request_id else None,
    )


def install_error_handlers(app: FastAPI) -> None:
    """Install the v2 error envelope for HTTP and validation errors."""

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return error_envelope(request, exc.status_code, _safe_detail(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return error_envelope(
            request,
            422,
            exc.errors(),
            message="request validation failed",
        )
