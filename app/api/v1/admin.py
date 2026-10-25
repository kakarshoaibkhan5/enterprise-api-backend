"""Administrative endpoints (role: admin)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.api.deps import CurrentAdmin, DbSession, PageParamsDep
from app.api.errors import COMMON_RESPONSES
from app.core.pagination import Page
from app.models.enums import UserRole
from app.schemas.user import AdminStats, UserAdminUpdate, UserRead
from app.services import users as user_service

router = APIRouter(prefix="/admin", tags=["Administration"], responses=COMMON_RESPONSES)


@router.get(
    "/users",
    response_model=Page[UserRead],
    summary="List all users",
    description="Paginated directory search across every account. Requires the admin role.",
)
async def list_users(
    db: DbSession,
    _admin: CurrentAdmin,
    params: PageParamsDep,
    search: str | None = Query(default=None, max_length=120, description="Email or name fragment"),
    role: UserRole | None = Query(default=None),
    is_active: bool | None = Query(default=None),
) -> Page[UserRead]:
    page = await user_service.list_users(db, params, search=search, role=role, is_active=is_active)
    return Page.create([UserRead.model_validate(user) for user in page.items], page.total, params)


@router.patch(
    "/users/{user_id}",
    response_model=UserRead,
    summary="Update a user's role or status",
    description=(
        "Deactivating a user or changing their role revokes their sessions. "
        "Self-lockout and removal of the last active administrator are prevented."
    ),
)
async def update_user(
    user_id: uuid.UUID,
    payload: UserAdminUpdate,
    db: DbSession,
    admin: CurrentAdmin,
) -> UserRead:
    target = await user_service.get_user_or_404(db, user_id)
    updated = await user_service.admin_update_user(db, admin, target, payload)
    return UserRead.model_validate(updated)


@router.get(
    "/stats",
    response_model=AdminStats,
    summary="Platform-wide counters",
)
async def stats(db: DbSession, _admin: CurrentAdmin) -> AdminStats:
    return await user_service.admin_stats(db)
