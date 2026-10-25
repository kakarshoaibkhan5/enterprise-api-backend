"""Authentication request/response schemas."""

from __future__ import annotations

import re
import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.schemas.common import UTCDatetime
from app.schemas.user import UserRead

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128


def validate_password_strength(password: str) -> str:
    """Reject trivially weak passwords while staying NIST-aligned.

    We deliberately do not require symbols or mixed case: length is the main
    driver of entropy. We do require a letter and a digit to block the most
    common leaked-password shapes.
    """
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters long")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise ValueError(f"Password must be at most {PASSWORD_MAX_LENGTH} characters long")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ValueError("Password must contain at least one letter and one digit")
    return password


class _EmailMixin(BaseModel):
    email: EmailStr = Field(description="Account email address", examples=["ada@example.com"])

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class RegisterRequest(_EmailMixin):
    password: str = Field(
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description="At least 8 characters, with a letter and a digit",
        examples=["CorrectHorse9"],
    )
    full_name: str | None = Field(default=None, max_length=120, examples=["Ada Lovelace"])

    _validate_password = field_validator("password")(validate_password_strength)

    @field_validator("full_name")
    @classmethod
    def _clean_full_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None


class LoginRequest(_EmailMixin):
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class TokenPair(BaseModel):
    """Access + refresh token pair returned by the login/refresh endpoints."""

    access_token: str = Field(description="Short-lived bearer token for the Authorization header")
    refresh_token: str = Field(description="Long-lived token used to obtain a new pair")
    token_type: str = Field(default="bearer", description="Always 'bearer'")
    expires_in: int = Field(description="Access token lifetime in seconds")
    refresh_expires_in: int = Field(description="Refresh token lifetime in seconds")


class TokenResponse(BaseModel):
    """OAuth2-compatible response for the Swagger 'Authorize' dialog."""

    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=16, description="A refresh token previously issued")


class LogoutRequest(BaseModel):
    refresh_token: str | None = Field(
        default=None,
        description="Specific refresh token to revoke. Omit to revoke every session.",
    )


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)

    _validate_password = field_validator("new_password")(validate_password_strength)


class AccountDeleteRequest(BaseModel):
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    confirm: bool = Field(
        default=True,
        description="Must be true; guards against accidental destructive calls",
    )


class UserSummary(BaseModel):
    """Compact user representation embedded in other resources."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None = None


class AuthResponse(BaseModel):
    """Returned by register/login/refresh so clients need a single round trip."""

    user: UserRead
    tokens: TokenPair


class TokenClaims(BaseModel):
    """Decoded access-token claims, exposed for debugging via /auth/verify."""

    sub: str
    type: str
    jti: str
    ver: int
    iat: UTCDatetime
    exp: UTCDatetime
