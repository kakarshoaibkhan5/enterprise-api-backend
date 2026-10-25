"""Domain exceptions and the canonical error envelope.

Every error returned by the API has the same shape::

    {
      "error": {
        "code": "not_found",
        "message": "Project not found",
        "details": [],
        "request_id": "9f2c..."
      }
    }

Using a single envelope keeps client error handling predictable and lets
support staff correlate a response with server logs via ``request_id``.
"""

from __future__ import annotations

from typing import Any

from fastapi import status


class AppError(Exception):
    """Base class for all expected application errors."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    message: str = "Internal server error"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details = details or []
        # Inherit the class-level default headers (e.g. WWW-Authenticate) unless
        # this instance supplies its own.
        self.headers = headers if headers is not None else dict(getattr(type(self), "headers", {}))
        super().__init__(self.message)


class BadRequestError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "bad_request"
    message = "The request could not be processed"


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "authentication_required"
    message = "Authentication is required"
    headers = {"WWW-Authenticate": "Bearer"}


class InvalidCredentialsError(AuthenticationError):
    code = "invalid_credentials"
    message = "Incorrect email or password"


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"
    message = "You do not have permission to perform this action"


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "The requested resource was not found"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    message = "The resource already exists or conflicts with the current state"


class UnprocessableEntityError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "validation_error"
    message = "The request was well-formed but failed validation"


class RateLimitError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limit_exceeded"
    message = "Too many requests; please retry later"

    def __init__(self, message: str | None = None, *, retry_after: int = 60) -> None:
        super().__init__(
            message,
            details=[{"retry_after": retry_after}],
            headers={"Retry-After": str(retry_after)},
        )


class PayloadTooLargeError(AppError):
    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    code = "payload_too_large"
    message = "Request body is too large"


class ServiceUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"
    message = "A required dependency is unavailable"
