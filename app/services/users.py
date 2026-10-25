"""User management and administrative statistics."""

from __future__ import annotations

import logging

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.pagination import Page, PageParams
from app.models.enums import UserRole
from app.models.project import Project
from app.models.task import Task
from app.models.user import User
from app.schemas.user import AdminStats, UserAdminUpdate, UserUpdate

logger = logging.getLogger(__name__)


async def _email_taken(db: AsyncSession, email: str, *, exclude_user_id: object) -> bool:
    result = await db.execute(
        select(User.id).where(func.lower(User.email) == email.lower(), User.id != exclude_user_id)
    )
    return result.first() is not None


async def update_profile(db: AsyncSession, user: User, data: UserUpdate) -> User:
    changes = data.model_dump(exclude_unset=True)
    if "email" in changes and changes["email"] and changes["email"] != user.email:
        if await _email_taken(db, changes["email"], exclude_user_id=user.id):
            raise ConflictError("That email is already in use", code="email_already_registered")
        user.email = changes["email"]
    if "full_name" in changes:
        user.full_name = changes["full_name"]
    await db.commit()
    await db.refresh(user)
    return user


async def list_users(
    db: AsyncSession,
    params: PageParams,
    *,
    search: str | None = None,
    role: UserRole | None = None,
    is_active: bool | None = None,
) -> Page[User]:
    conditions = []
    if search:
        pattern = f"%{search.strip().lower()}%"
        conditions.append(
            or_(
                func.lower(User.email).like(pattern),
                func.lower(User.full_name).like(pattern),
            )
        )
    if role is not None:
        conditions.append(User.role == role)
    if is_active is not None:
        conditions.append(User.is_active.is_(is_active))

    total = await db.scalar(select(func.count()).select_from(User).where(*conditions)) or 0
    result = await db.execute(
        select(User)
        .where(*conditions)
        .order_by(User.created_at.desc())
        .offset(params.offset)
        .limit(params.limit)
    )
    return Page.create(result.scalars().all(), total, params)


async def admin_update_user(
    db: AsyncSession, actor: User, target: User, data: UserAdminUpdate
) -> User:
    changes = data.model_dump(exclude_unset=True)

    if target.id == actor.id and (
        changes.get("is_active") is False or changes.get("role") == UserRole.MEMBER
    ):
        raise BadRequestError(
            "You cannot deactivate or demote your own administrator account",
            code="self_lockout_prevented",
        )

    if target.role == UserRole.ADMIN and (
        changes.get("is_active") is False or changes.get("role") == UserRole.MEMBER
    ):
        remaining_admins = await db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == UserRole.ADMIN, User.is_active.is_(True), User.id != target.id)
        )
        if not remaining_admins:
            raise ConflictError(
                "At least one active administrator must remain",
                code="last_admin_protected",
            )

    for field, value in changes.items():
        setattr(target, field, value)

    if changes.get("is_active") is False or changes.get("role") is not None:
        target.token_version += 1

    await db.commit()
    await db.refresh(target)
    logger.info("Administrator %s updated user %s: %s", actor.id, target.id, list(changes))
    return target


async def get_user_or_404(db: AsyncSession, user_id: object) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found", code="user_not_found")
    return user


async def delete_account(db: AsyncSession, user: User) -> None:
    """Hard-delete the current user; cascades remove projects and memberships."""
    await db.delete(user)
    await db.commit()


async def admin_stats(db: AsyncSession) -> AdminStats:
    total_users = await db.scalar(select(func.count()).select_from(User)) or 0
    active_users = (
        await db.scalar(select(func.count()).select_from(User).where(User.is_active.is_(True))) or 0
    )
    admin_users = (
        await db.scalar(select(func.count()).select_from(User).where(User.role == UserRole.ADMIN))
        or 0
    )
    total_projects = await db.scalar(select(func.count()).select_from(Project)) or 0
    total_tasks = await db.scalar(select(func.count()).select_from(Task)) or 0
    return AdminStats(
        total_users=total_users,
        active_users=active_users,
        admin_users=admin_users,
        total_projects=total_projects,
        total_tasks=total_tasks,
    )
