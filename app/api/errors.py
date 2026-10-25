"""Translate exceptions into the canonical error envelope."""

from __future__ import annotations

import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppError
from app.core.logging import request_id_ctx
from app.schemas.common import ErrorBody, ErrorDetail, ErrorResponse

logger = logging.getLogger(__name__)

_STATUS_CODES = {
    400: "bad_request",
    401: "authentication_required",
    403: "permission_denied",
    404: "not_found",
    405: "method_not_allowed",
    406: "not_acceptable",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limit_exceeded",
    500: "internal_error",
    503: "service_unavailable",
}


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[ErrorDetail | dict] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            details=[
                detail if isinstance(detail, ErrorDetail) else ErrorDetail(**detail)
                for detail in details or []
            ],
            request_id=request_id_ctx.get(),
        )
    )
    return JSONResponse(
        status_code=status_code, content=body.model_dump(mode="json"), headers=headers
    )


def _validation_details(exc: RequestValidationError) -> list[dict]:
    details = []
    for error in exc.errors():
        location = [
            str(part) for part in error.get("loc", ()) if part not in ("body", "query", "path")
        ]
        field = ".".join(location) or None
        details.append(
            {
                "field": field,
                "message": error.get("msg", "Invalid value"),
                "type": error.get("type"),
            }
        )
    return details


COMMON_RESPONSES: dict[int | str, dict] = {
    401: {"model": ErrorResponse, "description": "Missing, invalid, or revoked credentials"},
    403: {"model": ErrorResponse, "description": "Authenticated but not permitted"},
    404: {"model": ErrorResponse, "description": "Resource not found"},
    409: {"model": ErrorResponse, "description": "Conflicting state"},
    422: {"model": ErrorResponse, "description": "Request validation failed"},
    429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
    500: {"model": ErrorResponse, "description": "Unexpected server error"},
}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error("Application error: %s", exc.message)
        return error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
            headers=exc.headers or None,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(
            status_code=422,
            code="validation_error",
            message="The request body or parameters failed validation",
            details=_validation_details(exc),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code).phrase
        headers = dict(exc.headers or {})
        if exc.status_code == 401 and "WWW-Authenticate" not in headers:
            headers["WWW-Authenticate"] = "Bearer"
        return error_response(
            status_code=exc.status_code,
            code=_STATUS_CODES.get(exc.status_code, "http_error"),
            message=message,
            headers=headers or None,
        )

    @app.exception_handler(IntegrityError)
    async def _integrity_handler(_request: Request, exc: IntegrityError) -> JSONResponse:
        logger.warning("Database integrity error: %s", exc)
        return error_response(
            status_code=409,
            code="conflict",
            message="The operation conflicts with existing data",
        )

    @app.exception_handler(ResponseValidationError)
    async def _response_validation_handler(
        _request: Request, exc: ResponseValidationError
    ) -> JSONResponse:
        logger.exception("Response validation failed: %s", exc)
        return error_response(
            status_code=500,
            code="internal_error",
            message="The server produced an unexpected response",
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception: %s", exc)
        return error_response(
            status_code=500,
            code="internal_error",
            message="An unexpected error occurred. Quote the request id when contacting support.",
        )
