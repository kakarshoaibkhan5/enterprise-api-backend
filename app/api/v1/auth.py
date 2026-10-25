"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import ClientInfoDep, CurrentUser, DbSession
from app.api.errors import COMMON_RESPONSES
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
)
from app.schemas.common import MessageResponse
from app.schemas.user import UserRead
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["Authentication"], responses=COMMON_RESPONSES)


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new account",
    description=(
        "Creates a member account and immediately returns an access/refresh token pair "
        "so clients do not need a second round trip."
    ),
)
async def register(
    payload: RegisterRequest,
    db: DbSession,
    client: ClientInfoDep,
) -> AuthResponse:
    user = await auth_service.register_user(db, payload)
    tokens = await auth_service.issue_token_pair(db, user, client)
    return AuthResponse(user=UserRead.model_validate(user), tokens=tokens)


@router.post(
    "/login",
    response_model=AuthResponse,
    summary="Log in with email and password (JSON)",
    description="Returns a fresh access token, refresh token, and the user profile.",
)
async def login(
    payload: LoginRequest,
    db: DbSession,
    client: ClientInfoDep,
) -> AuthResponse:
    return await auth_service.authenticate_and_issue(
        db, str(payload.email), payload.password, client
    )


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="OAuth2 password flow (used by Swagger UI)",
    description=(
        "Form-encoded OAuth2 password grant. Use the **Authorize** button with your "
        "email as the username. Prefer `/auth/login` for JSON clients."
    ),
)
async def login_for_access_token(
    db: DbSession,
    client: ClientInfoDep,
    form_data: OAuth2PasswordRequestForm = Depends(),
) -> TokenResponse:
    response = await auth_service.authenticate_and_issue(
        db, form_data.username, form_data.password, client
    )
    return TokenResponse(
        access_token=response.tokens.access_token,
        refresh_token=response.tokens.refresh_token,
        token_type=response.tokens.token_type,
        expires_in=response.tokens.expires_in,
    )


@router.post(
    "/refresh",
    response_model=AuthResponse,
    summary="Rotate a refresh token",
    description=(
        "Exchanges a refresh token for a new pair. Refresh tokens are single-use: "
        "replaying a revoked token terminates every session for the account."
    ),
)
async def refresh(
    payload: RefreshRequest,
    db: DbSession,
    client: ClientInfoDep,
) -> AuthResponse:
    return await auth_service.rotate_refresh_token(db, payload.refresh_token, client)


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Log out",
    description=(
        "Revokes the supplied refresh token. Omit `refresh_token` to revoke every "
        "refresh token for the account. Access tokens expire naturally; use "
        "`/auth/logout-all` to revoke them immediately."
    ),
)
async def logout(
    db: DbSession,
    user: CurrentUser,
    payload: LogoutRequest | None = Body(default=None),
) -> MessageResponse:
    await auth_service.logout(db, user, payload.refresh_token if payload else None)
    return MessageResponse(message="Logged out")


@router.post(
    "/logout-all",
    response_model=MessageResponse,
    summary="Log out of every device",
    description=(
        "Revokes all refresh tokens and bumps the token version, invalidating "
        "outstanding access tokens immediately."
    ),
)
async def logout_all(db: DbSession, user: CurrentUser) -> MessageResponse:
    await auth_service.logout_all(db, user)
    return MessageResponse(message="All sessions have been terminated")


@router.get(
    "/me",
    response_model=UserRead,
    summary="Current authenticated user",
    description="Convenience alias for `GET /users/me`.",
)
async def read_me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)
