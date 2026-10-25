"""HTTP middleware: request correlation, security headers, limits, rate limiting."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import Request, Response
from starlette.datastructures import MutableHeaders
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.core.logging import request_id_ctx
from app.core.ratelimit import SlidingWindowRateLimiter
from app.schemas.common import ErrorBody, ErrorResponse

logger = logging.getLogger(__name__)
settings = get_settings()

REQUEST_ID_HEADER = "X-Request-ID"


class RequestContextMiddleware:
    """Attach a request id to logs, error envelopes, and the response headers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope.get("headers") or [])
        supplied = incoming.get(b"x-request-id", b"").decode("latin-1").strip()
        request_id = supplied[:128] if supplied else uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        scope.setdefault("state", {})["request_id"] = request_id

        start = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_started = True
                headers = MutableHeaders(scope=message)
                headers.append(REQUEST_ID_HEADER, request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # Starlette's own error middleware sits outside user middleware, so a
            # truly unhandled exception would otherwise be rendered without a
            # request id. Emit the canonical envelope here while response headers
            # have not been sent yet.
            if response_started:
                raise
            logger.exception("Unhandled exception while processing request")
            status_code = 500
            payload = ErrorResponse(
                error=ErrorBody(
                    code="internal_error",
                    message=(
                        "An unexpected error occurred. "
                        "Quote the request id when contacting support."
                    ),
                    details=[],
                    request_id=request_id,
                )
            )
            await send(
                {
                    "type": "http.response.start",
                    "status": status_code,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (REQUEST_ID_HEADER.lower().encode(), request_id.encode()),
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": payload.model_dump_json().encode(),
                }
            )
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "%s %s -> %s (%.1f ms)",
                scope.get("method", "-"),
                scope.get("path", "-"),
                status_code,
                duration_ms,
            )
            request_id_ctx.reset(token)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Conservative browser hardening headers on every response."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self.csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' "
            "https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https://fastapi.tiangolo.com; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        )

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
            response.headers.setdefault("Content-Security-Policy", self.csp)
        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized payloads early using the Content-Length header.

    Chunked uploads without a length are bounded by the reverse proxy
    (``client_max_body_size`` in nginx, ``--limit-request-body`` in other
    frontends). Documented in docs/DEPLOYMENT.md.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        raw_length = request.headers.get("content-length")
        if raw_length:
            try:
                length = int(raw_length)
            except ValueError:
                return _error_json(400, "invalid_content_length", "Invalid Content-Length header")
            if length > self.max_bytes:
                return _error_json(
                    413,
                    "payload_too_large",
                    f"Request body exceeds the {self.max_bytes} byte limit",
                )
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window limiter for credential and report endpoints.

    Keyed by client IP + route. In-process only: for multi-worker deployments
    use a shared store (Redis) or enforce limits at the gateway.
    """

    _AUTH_PATHS = ("/auth/register", "/auth/login", "/auth/token", "/auth/refresh")

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self.auth_limiter = SlidingWindowRateLimiter(
            limit=settings.auth_rate_limit_per_minute, window_seconds=60
        )
        self.pdf_limiter = SlidingWindowRateLimiter(
            limit=settings.pdf_rate_limit_per_minute, window_seconds=60
        )

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not settings.rate_limit_enabled:
            return await call_next(request)

        path = request.url.path
        limiter = None
        if (
            request.method == "POST"
            and path.startswith(settings.api_v1_prefix)
            and path.endswith(self._AUTH_PATHS)
        ):
            limiter = self.auth_limiter
        elif request.method == "GET" and path.endswith("/report.pdf"):
            limiter = self.pdf_limiter

        if limiter is None:
            return await call_next(request)

        client = request.client.host if request.client else "unknown"
        allowed, retry_after = await limiter.hit(f"{client}:{path}")
        if not allowed:
            logger.warning("Rate limit exceeded for %s on %s", client, path)
            response = _error_json(
                429,
                "rate_limit_exceeded",
                "Too many requests. Please retry later.",
                headers={"Retry-After": str(retry_after)},
            )
            response.headers["X-RateLimit-Limit"] = str(limiter.limit)
            return response

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limiter.limit)
        return response


def _error_json(
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            details=[],
            request_id=request_id_ctx.get(),
        )
    )
    response = JSONResponse(
        status_code=status_code, content=body.model_dump(mode="json"), headers=headers
    )
    return response
