"""User schemas."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.enums import UserRole
from app.schemas.common import UTCDatetime


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None = None
    role: UserRole
    is_active: bool
    created_at: UTCDatetime
    updated_at: UTCDatetime
    last_login_at: UTCDatetime | None = None


class UserUpdate(BaseModel):
    email: EmailStr | None = Field(default=None, examples=["ada@example.com"])
    full_name: str | None = Field(default=None, max_length=120, examples=["Ada Lovelace"])

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: EmailStr | None) -> str | None:
        return str(value).strip().lower() if value is not None else None

    @field_validator("full_name")
    @classmethod
    def _clean_full_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None


class UserAdminUpdate(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None
    full_name: str | None = Field(default=None, max_length=120)

    @field_validator("full_name")
    @classmethod
    def _clean_full_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None


class AdminStats(BaseModel):
    total_users: int
    active_users: int
    admin_users: int
    total_projects: int
    total_tasks: int
