"""Authentication business logic: registration, login, tokens, sessions."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import (
    AuthenticationError,
    BadRequestError,
    ConflictError,
    InvalidCredentialsError,
)
from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.core.timeutils import as_utc, utcnow
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import (
    AuthResponse,
    RegisterRequest,
    TokenPair,
)
from app.schemas.user import UserRead

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass(slots=True)
class ClientInfo:
    """Best-effort client metadata stored alongside refresh tokens."""

    user_agent: str | None = None
    ip_address: str | None = None


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------
async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(func.lower(User.email) == email.lower()))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: object) -> User | None:
    return await db.get(User, user_id)


# ---------------------------------------------------------------------------
# Registration & login
# ---------------------------------------------------------------------------
async def register_user(db: AsyncSession, data: RegisterRequest) -> User:
    if await get_user_by_email(db, str(data.email)) is not None:
        raise ConflictError(
            "An account with this email already exists",
            code="email_already_registered",
        )

    user = User(
        email=str(data.email),
        hashed_password=hash_password(data.password),
        full_name=data.full_name,
    )
    db.add(user)
    try:
        await db.commit()
    except Exception as exc:  # pragma: no cover - unique constraint race
        await db.rollback()
        logger.warning("Registration race for %s: %s", data.email, exc)
        raise ConflictError(
            "An account with this email already exists",
            code="email_already_registered",
        ) from exc
    await db.refresh(user)
    logger.info("Registered user %s", user.id)
    return user


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User:
    user = await get_user_by_email(db, email)
    if user is None or not verify_password(password, user.hashed_password):
        raise InvalidCredentialsError()
    if not user.is_active:
        raise AuthenticationError(
            "This account has been deactivated. Contact an administrator.",
            code="account_disabled",
        )

    user.last_login_at = utcnow()
    await db.commit()
    await db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Token issuing / rotation
# ---------------------------------------------------------------------------
def _build_pair(user: User) -> tuple[TokenPair, str, datetime]:
    access_token = create_access_token(str(user.id), user.token_version)
    refresh_token, _jti, refresh_expires_at = create_refresh_token(str(user.id), user.token_version)
    pair = TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.access_token_ttl_seconds,
        refresh_expires_in=settings.refresh_token_ttl_seconds,
    )
    return pair, refresh_token, refresh_expires_at


def _record_refresh_token(
    user: User, refresh_token: str, expires_at: datetime, client: ClientInfo
) -> RefreshToken:
    return RefreshToken(
        user_id=user.id,
        token_hash=hash_token(refresh_token),
        expires_at=expires_at,
        user_agent=(client.user_agent or "")[:255] or None,
        ip_address=(client.ip_address or "")[:45] or None,
    )


async def issue_token_pair(
    db: AsyncSession, user: User, client: ClientInfo | None = None
) -> TokenPair:
    client = client or ClientInfo()
    pair, refresh_token, refresh_expires_at = _build_pair(user)
    db.add(_record_refresh_token(user, refresh_token, refresh_expires_at, client))
    await db.commit()
    return pair


async def authenticate_and_issue(
    db: AsyncSession, email: str, password: str, client: ClientInfo | None = None
) -> AuthResponse:
    user = await authenticate_user(db, email, password)
    pair = await issue_token_pair(db, user, client)
    return AuthResponse(user=UserRead.model_validate(user), tokens=pair)


async def _revoke_all_for_user(db: AsyncSession, user_id: object, *, now: datetime) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )


async def rotate_refresh_token(
    db: AsyncSession, refresh_token: str, client: ClientInfo | None = None
) -> AuthResponse:
    """Exchange a refresh token for a new pair, detecting token reuse.

    The old token is revoked atomically. If a token that is already revoked is
    presented, we assume it was stolen and terminate every session for the
    user (revoke all refresh tokens + bump ``token_version``).
    """
    client = client or ClientInfo()
    try:
        payload = decode_token(refresh_token, expected_type="refresh")
    except TokenError as exc:
        raise AuthenticationError(str(exc), code="invalid_refresh_token") from exc

    record = (
        await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(refresh_token))
        )
    ).scalar_one_or_none()
    if record is None:
        raise AuthenticationError("Refresh token is invalid", code="invalid_refresh_token")

    now = utcnow()
    user = await db.get(User, record.user_id)
    if user is None:
        raise AuthenticationError("Refresh token is invalid", code="invalid_refresh_token")

    if record.revoked_at is not None:
        await _revoke_all_for_user(db, user.id, now=now)
        user.token_version += 1
        await db.commit()
        logger.warning("Refresh token reuse detected for user %s; sessions revoked", user.id)
        raise AuthenticationError(
            "This refresh token has already been used. All sessions were terminated.",
            code="refresh_token_reuse",
        )

    if as_utc(record.expires_at) <= now:
        record.revoked_at = now
        await db.commit()
        raise AuthenticationError("Refresh token has expired", code="refresh_token_expired")

    if not user.is_active:
        await _revoke_all_for_user(db, user.id, now=now)
        await db.commit()
        raise AuthenticationError("This account has been deactivated", code="account_disabled")

    if payload.get("ver") != user.token_version:
        await _revoke_all_for_user(db, user.id, now=now)
        await db.commit()
        raise AuthenticationError("Refresh token is no longer valid", code="invalid_refresh_token")

    # Atomic single-use enforcement: only one concurrent request can revoke the
    # row, everyone else is treated as a reuse attempt.
    revoked = await db.execute(
        update(RefreshToken)
        .where(RefreshToken.id == record.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    if revoked.rowcount != 1:
        await _revoke_all_for_user(db, user.id, now=now)
        user.token_version += 1
        await db.commit()
        raise AuthenticationError(
            "This refresh token has already been used. All sessions were terminated.",
            code="refresh_token_reuse",
        )

    pair, new_refresh, new_expires = _build_pair(user)
    db.add(_record_refresh_token(user, new_refresh, new_expires, client))
    await db.commit()
    await db.refresh(user)
    return AuthResponse(user=UserRead.model_validate(user), tokens=pair)


# ---------------------------------------------------------------------------
# Logout & password management
# ---------------------------------------------------------------------------
async def revoke_refresh_token(db: AsyncSession, user: User, refresh_token: str) -> None:
    record = (
        await db.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == hash_token(refresh_token),
                RefreshToken.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if record is None:
        raise BadRequestError("Refresh token does not belong to this user", code="invalid_token")
    if record.revoked_at is None:
        record.revoked_at = utcnow()
        await db.commit()


async def logout(db: AsyncSession, user: User, refresh_token: str | None) -> None:
    if refresh_token:
        await revoke_refresh_token(db, user, refresh_token)
        return
    await _revoke_all_for_user(db, user.id, now=utcnow())
    await db.commit()


async def logout_all(db: AsyncSession, user: User) -> None:
    """Terminate every session, including outstanding access tokens."""
    await _revoke_all_for_user(db, user.id, now=utcnow())
    user.token_version += 1
    await db.commit()


async def change_password(db: AsyncSession, user: User, current: str, new: str) -> None:
    if not verify_password(current, user.hashed_password):
        raise BadRequestError("Current password is incorrect", code="invalid_password")
    if current == new:
        raise BadRequestError(
            "New password must be different from the current password", code="password_unchanged"
        )
    user.hashed_password = hash_password(new)
    user.token_version += 1
    await _revoke_all_for_user(db, user.id, now=utcnow())
    await db.commit()
    logger.info("Password changed for user %s; sessions revoked", user.id)
