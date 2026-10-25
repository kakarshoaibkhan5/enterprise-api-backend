"""Reusable FastAPI dependencies: auth, database, pagination, project access."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.core.pagination import PageParams
from app.core.security import TokenError, decode_token
from app.db.session import get_db
from app.models.enums import ProjectRole, UserRole
from app.models.user import User
from app.services.auth import ClientInfo
from app.services.projects import ProjectContext, resolve_project_access

settings = get_settings()

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.api_v1_prefix}/auth/token",
    auto_error=False,
    description="JWT access token obtained from /auth/login or /auth/token",
)

DbSession = Annotated[AsyncSession, Depends(get_db)]
PageParamsDep = Annotated[PageParams, Depends()]
BearerToken = Annotated[str | None, Depends(oauth2_scheme)]


def get_client_info(request: Request) -> ClientInfo:
    """Client metadata recorded with refresh tokens (audit trail)."""
    return ClientInfo(
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )


ClientInfoDep = Annotated[ClientInfo, Depends(get_client_info)]


async def get_current_user(
    request: Request,
    db: DbSession,
    token: BearerToken = None,
) -> User:
    """Validate the bearer token and return the authenticated user.

    Rejects tokens whose ``ver`` claim no longer matches the user's
    ``token_version`` so password changes and logout-all take effect
    immediately, without waiting for token expiry.
    """
    if not token:
        raise AuthenticationError(
            "Missing bearer token. Send an 'Authorization: Bearer <token>' header."
        )
    try:
        payload = decode_token(token, expected_type="access")
    except TokenError as exc:
        raise AuthenticationError(str(exc), code="invalid_token") from exc

    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, TypeError) as exc:
        raise AuthenticationError("Token subject is invalid", code="invalid_token") from exc

    user = await db.get(User, user_id)
    if user is None:
        raise AuthenticationError("The account no longer exists", code="invalid_token")
    if not user.is_active:
        raise AuthenticationError("This account has been deactivated", code="account_disabled")
    if payload.get("ver") != user.token_version:
        raise AuthenticationError(
            "This session has been revoked. Please sign in again.", code="token_revoked"
        )

    request.state.user = user
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_admin(user: CurrentUser) -> User:
    if user.role is not UserRole.ADMIN:
        raise PermissionDeniedError("Administrator privileges are required", code="admin_required")
    return user


CurrentAdmin = Annotated[User, Depends(get_current_admin)]


def require_project_role(min_role: ProjectRole):  # noqa: ANN201 - returns dependency
    """Build a dependency that loads a project and checks a minimum role."""

    async def dependency(
        project_id: uuid.UUID,
        db: DbSession,
        user: CurrentUser,
    ) -> ProjectContext:
        return await resolve_project_access(db, project_id, user, min_role)

    return dependency


ProjectViewer = Annotated[ProjectContext, Depends(require_project_role(ProjectRole.VIEWER))]
ProjectEditor = Annotated[ProjectContext, Depends(require_project_role(ProjectRole.EDITOR))]
ProjectOwner = Annotated[ProjectContext, Depends(require_project_role(ProjectRole.OWNER))]
