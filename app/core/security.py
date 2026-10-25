"""Password hashing and JSON Web Token helpers."""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta
from typing import Any, Literal

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.timeutils import utcnow

settings = get_settings()

TokenType = Literal["access", "refresh"]


class TokenError(Exception):
    """Raised when a token is malformed, expired, or of the wrong type."""


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    """Hash a password with bcrypt using the configured work factor."""
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(rounds=settings.bcrypt_rounds)
    ).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Constant-time password verification that never raises on bad input."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
def _encode_token(
    subject: str, token_type: TokenType, expires_delta: timedelta, token_version: int
) -> tuple[str, str, Any]:
    """Return ``(encoded_token, jti, expires_at)``."""
    issued_at = utcnow()
    expires_at = issued_at + expires_delta
    jti = uuid.uuid4().hex
    payload = {
        "sub": subject,
        "type": token_type,
        "jti": jti,
        "ver": token_version,
        "iat": issued_at,
        "exp": expires_at,
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token, jti, expires_at


def create_access_token(subject: str, token_version: int = 0) -> str:
    """Create a short-lived access token for ``subject`` (the user id)."""
    token, _, _ = _encode_token(
        subject,
        "access",
        timedelta(minutes=settings.access_token_expire_minutes),
        token_version,
    )
    return token


def create_refresh_token(subject: str, token_version: int = 0) -> tuple[str, str, Any]:
    """Create a refresh token and return ``(token, jti, expires_at)``."""
    return _encode_token(
        subject,
        "refresh",
        timedelta(days=settings.refresh_token_expire_days),
        token_version,
    )


def decode_token(token: str, *, expected_type: TokenType) -> dict[str, Any]:
    """Decode and validate a token, enforcing its type.

    Raises:
        TokenError: if the signature, expiry, claims, or type are invalid.
    """
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "iat", "sub", "type", "ver", "jti"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("Token is invalid") from exc

    if payload.get("type") != expected_type:
        raise TokenError(f"Expected token type {expected_type!r}")
    if not isinstance(payload.get("sub"), str):
        raise TokenError("Token subject is invalid")
    if not isinstance(payload.get("ver"), int):
        raise TokenError("Token version is invalid")
    return payload


def hash_token(token: str) -> str:
    """Hash a token for at-rest storage (SHA-256, hex encoded)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
