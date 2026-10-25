"""Reusable schema building blocks: error envelope, UTC datetimes, messages."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from app.core.timeutils import as_utc


def _coerce_utc(value: Any) -> Any:
    """Normalise datetimes read from databases that drop timezone info (SQLite)."""
    if isinstance(value, datetime):
        return as_utc(value)
    return value


UTCDatetime = Annotated[datetime, BeforeValidator(_coerce_utc)]


class ErrorDetail(BaseModel):
    """A single, field-level or contextual error detail."""

    field: str | None = Field(default=None, description="JSON path of the offending field")
    message: str = Field(description="Human readable explanation")
    type: str | None = Field(default=None, description="Machine readable error type")


class ErrorBody(BaseModel):
    code: str = Field(description="Stable, machine readable error code")
    message: str = Field(description="Human readable summary")
    details: list[ErrorDetail] = Field(default_factory=list)
    request_id: str = Field(description="Correlation id, also returned in the X-Request-ID header")


class ErrorResponse(BaseModel):
    """The single error envelope returned by every endpoint."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": {
                    "code": "not_found",
                    "message": "Project not found",
                    "details": [],
                    "request_id": "0f8fad5b-d9cb-469f-a165-70867728950e",
                }
            }
        }
    )

    error: ErrorBody


class MessageResponse(BaseModel):
    message: str = Field(description="Human readable status message")


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = Field(description="Overall service status")
    version: str
    environment: str
    database: Literal["up", "down"] = Field(description="Database connectivity")


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"
