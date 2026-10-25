"""Project, membership and project-statistics business logic."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    UnprocessableEntityError,
)
from app.core.pagination import Page, PageParams
from app.core.timeutils import utcnow
from app.models.enums import (
    PROJECT_ROLE_RANK,
    ProjectRole,
    ProjectStatus,
    TaskPriority,
    TaskStatus,
    UserRole,
)
from app.models.project import Project, ProjectMember
from app.models.task import Task
from app.models.user import User
from app.schemas.project import (
    ProjectCreate,
    ProjectMemberCreate,
    ProjectMemberRead,
    ProjectMemberUpdate,
    ProjectRead,
    ProjectStats,
    ProjectUpdate,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProjectContext:
    """A project together with the requesting user's effective role."""

    project: Project
    role: ProjectRole
    user: User

    @property
    def is_owner(self) -> bool:
        return self.role is ProjectRole.OWNER


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------
async def resolve_project_access(
    db: AsyncSession,
    project_id: uuid.UUID,
    user: User,
    min_role: ProjectRole = ProjectRole.VIEWER,
) -> ProjectContext:
    """Load a project and assert the user has at least ``min_role``.

    Non-members receive ``404`` rather than ``403`` so the API does not leak
    the existence of projects belonging to other tenants.
    """
    project = (
        await db.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if project is None:
        raise NotFoundError("Project not found", code="project_not_found")

    if user.role is UserRole.ADMIN:
        return ProjectContext(project=project, role=ProjectRole.OWNER, user=user)

    if project.owner_id == user.id:
        return ProjectContext(project=project, role=ProjectRole.OWNER, user=user)

    membership_role = await db.scalar(
        select(ProjectMember.role).where(
            ProjectMember.project_id == project.id, ProjectMember.user_id == user.id
        )
    )
    if membership_role is None:
        raise NotFoundError("Project not found", code="project_not_found")

    if PROJECT_ROLE_RANK[membership_role] < PROJECT_ROLE_RANK[min_role]:
        raise PermissionDeniedError(
            f"This action requires the '{min_role.value}' role on the project",
            code="insufficient_project_role",
        )
    return ProjectContext(project=project, role=membership_role, user=user)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------
def _serialize_project(
    project: Project, role: ProjectRole | None, member_count: int, task_count: int
) -> ProjectRead:
    model = ProjectRead.model_validate(project)
    return model.model_copy(
        update={
            "owner_email": project.owner.email if project.owner else None,
            "role": role,
            "member_count": member_count,
            "task_count": task_count,
        }
    )


async def _load_counts(
    db: AsyncSession, project_ids: list[uuid.UUID]
) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, int]]:
    if not project_ids:
        return {}, {}
    member_rows = await db.execute(
        select(ProjectMember.project_id, func.count())
        .where(ProjectMember.project_id.in_(project_ids))
        .group_by(ProjectMember.project_id)
    )
    task_rows = await db.execute(
        select(Task.project_id, func.count())
        .where(Task.project_id.in_(project_ids))
        .group_by(Task.project_id)
    )
    return dict(member_rows.all()), dict(task_rows.all())


async def _reload(db: AsyncSession, project_id: uuid.UUID) -> Project:
    project = (await db.execute(select(Project).where(Project.id == project_id))).scalar_one()
    return project


# ---------------------------------------------------------------------------
# Projects CRUD
# ---------------------------------------------------------------------------
async def list_projects(
    db: AsyncSession,
    user: User,
    params: PageParams,
    *,
    search: str | None = None,
    status: ProjectStatus | None = None,
    sort: str = "created_at",
    order: str = "desc",
) -> Page[ProjectRead]:
    conditions = []
    if user.role is not UserRole.ADMIN:
        conditions.append(
            or_(
                Project.owner_id == user.id,
                Project.id.in_(
                    select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
                ),
            )
        )
    if search:
        pattern = f"%{search.strip().lower()}%"
        conditions.append(
            or_(
                func.lower(Project.name).like(pattern),
                func.lower(Project.description).like(pattern),
            )
        )
    if status is not None:
        conditions.append(Project.status == status)

    total = await db.scalar(select(func.count()).select_from(Project).where(*conditions)) or 0

    sort_columns = {
        "created_at": Project.created_at,
        "updated_at": Project.updated_at,
        "name": Project.name,
        "due_date": Project.due_date,
        "status": Project.status,
    }
    column = sort_columns.get(sort, Project.created_at)
    direction = column.asc() if order == "asc" else column.desc()

    rows = await db.execute(
        select(Project)
        .where(*conditions)
        .order_by(direction, Project.id)
        .offset(params.offset)
        .limit(params.limit)
    )
    projects = list(rows.scalars().unique().all())
    ids = [project.id for project in projects]
    member_counts, task_counts = await _load_counts(db, ids)

    roles: dict[uuid.UUID, ProjectRole] = {}
    if ids:
        role_rows = await db.execute(
            select(ProjectMember.project_id, ProjectMember.role).where(
                ProjectMember.user_id == user.id, ProjectMember.project_id.in_(ids)
            )
        )
        roles = dict(role_rows.all())

    items = []
    for project in projects:
        if user.role is UserRole.ADMIN or project.owner_id == user.id:
            role = ProjectRole.OWNER
        else:
            role = roles.get(project.id)
        items.append(
            _serialize_project(
                project, role, member_counts.get(project.id, 0), task_counts.get(project.id, 0)
            )
        )
    return Page.create(items, total, params)


async def create_project(db: AsyncSession, user: User, data: ProjectCreate) -> ProjectRead:
    project = Project(
        owner_id=user.id,
        name=data.name,
        description=data.description,
        status=data.status,
        start_date=data.start_date,
        due_date=data.due_date,
    )
    db.add(project)
    await db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role=ProjectRole.OWNER))
    await db.commit()
    logger.info("User %s created project %s", user.id, project.id)
    project = await _reload(db, project.id)
    return _serialize_project(project, ProjectRole.OWNER, 1, 0)


async def get_project_read(db: AsyncSession, context: ProjectContext) -> ProjectRead:
    project = await _reload(db, context.project.id)
    member_counts, task_counts = await _load_counts(db, [project.id])
    return _serialize_project(
        project,
        context.role,
        member_counts.get(project.id, 0),
        task_counts.get(project.id, 0),
    )


async def update_project(
    db: AsyncSession, context: ProjectContext, data: ProjectUpdate
) -> ProjectRead:
    project = context.project
    changes = data.model_dump(exclude_unset=True)

    new_start = changes.get("start_date", project.start_date)
    new_due = changes.get("due_date", project.due_date)
    if new_start and new_due and new_due < new_start:
        raise UnprocessableEntityError(
            "due_date must be on or after start_date",
            details=[
                {
                    "field": "due_date",
                    "message": "due_date must be on or after start_date",
                    "type": "value_error",
                }
            ],
        )

    for field, value in changes.items():
        setattr(project, field, value)
    project.updated_at = utcnow()
    await db.commit()
    return await get_project_read(db, context)


async def delete_project(db: AsyncSession, context: ProjectContext) -> None:
    if not context.is_owner:
        raise PermissionDeniedError(
            "Only the project owner can delete a project", code="owner_required"
        )
    project_id = context.project.id
    await db.delete(context.project)
    await db.commit()
    logger.info("Project %s deleted by user %s", project_id, context.user.id)


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------
def _member_read(member: ProjectMember) -> ProjectMemberRead:
    return ProjectMemberRead(
        id=member.id,
        project_id=member.project_id,
        user_id=member.user_id,
        email=member.user.email,
        full_name=member.user.full_name,
        role=member.role,
        joined_at=member.joined_at,
    )


async def list_members(db: AsyncSession, project_id: uuid.UUID) -> list[ProjectMemberRead]:
    role_order = case(
        (ProjectMember.role == ProjectRole.OWNER, 0),
        (ProjectMember.role == ProjectRole.EDITOR, 1),
        else_=2,
    )
    rows = await db.execute(
        select(ProjectMember)
        .where(ProjectMember.project_id == project_id)
        .order_by(role_order, ProjectMember.joined_at)
    )
    return [_member_read(member) for member in rows.scalars().unique().all()]


async def add_member(
    db: AsyncSession, context: ProjectContext, data: ProjectMemberCreate
) -> ProjectMemberRead:
    if not context.is_owner:
        raise PermissionDeniedError(
            "Only the project owner can manage members", code="owner_required"
        )

    target = (
        await db.execute(select(User).where(func.lower(User.email) == str(data.email).lower()))
    ).scalar_one_or_none()
    if target is None:
        raise NotFoundError("No user is registered with that email", code="user_not_found")
    if not target.is_active:
        raise BadRequestError("That user account is deactivated", code="account_disabled")

    existing = await db.scalar(
        select(ProjectMember.id).where(
            ProjectMember.project_id == context.project.id, ProjectMember.user_id == target.id
        )
    )
    if existing is not None:
        raise ConflictError("That user is already a member of this project", code="already_member")

    member = ProjectMember(project_id=context.project.id, user_id=target.id, role=data.role)
    db.add(member)
    await db.commit()
    member = (
        await db.execute(select(ProjectMember).where(ProjectMember.id == member.id))
    ).scalar_one()
    logger.info("User %s added to project %s", target.id, context.project.id)
    return _member_read(member)


async def get_membership_or_404(
    db: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID
) -> ProjectMember:
    member = (
        await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise NotFoundError("Project member not found", code="member_not_found")
    return member


async def update_member(
    db: AsyncSession,
    context: ProjectContext,
    user_id: uuid.UUID,
    data: ProjectMemberUpdate,
) -> ProjectMemberRead:
    if not context.is_owner:
        raise PermissionDeniedError(
            "Only the project owner can manage members", code="owner_required"
        )
    if user_id == context.project.owner_id:
        raise BadRequestError(
            "The project owner's role cannot be changed", code="owner_role_immutable"
        )

    member = await get_membership_or_404(db, context.project.id, user_id)
    member.role = data.role
    await db.commit()
    member = (
        await db.execute(select(ProjectMember).where(ProjectMember.id == member.id))
    ).scalar_one()
    return _member_read(member)


async def remove_member(db: AsyncSession, context: ProjectContext, user_id: uuid.UUID) -> None:
    if not context.is_owner and user_id != context.user.id:
        raise PermissionDeniedError(
            "Only the project owner can remove other members", code="owner_required"
        )
    if user_id == context.project.owner_id:
        raise BadRequestError(
            "The project owner cannot be removed; delete the project instead",
            code="owner_cannot_leave",
        )

    member = await get_membership_or_404(db, context.project.id, user_id)
    await db.delete(member)
    await db.commit()


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
async def project_stats(db: AsyncSession, project_id: uuid.UUID) -> ProjectStats:
    status_rows = await db.execute(
        select(Task.status, func.count()).where(Task.project_id == project_id).group_by(Task.status)
    )
    priority_rows = await db.execute(
        select(Task.priority, func.count())
        .where(Task.project_id == project_id)
        .group_by(Task.priority)
    )
    by_status = {status.value: 0 for status in TaskStatus}
    by_status.update({status.value: count for status, count in status_rows.all()})
    by_priority = {priority.value: 0 for priority in TaskPriority}
    by_priority.update({priority.value: count for priority, count in priority_rows.all()})

    total = sum(by_status.values())
    completed = by_status[TaskStatus.DONE.value]
    overdue = (
        await db.scalar(
            select(func.count())
            .select_from(Task)
            .where(
                Task.project_id == project_id,
                Task.status != TaskStatus.DONE,
                Task.due_date.is_not(None),
                Task.due_date < date.today(),
            )
        )
        or 0
    )

    return ProjectStats(
        project_id=project_id,
        total_tasks=total,
        by_status=by_status,
        by_priority=by_priority,
        completed_tasks=completed,
        overdue_tasks=overdue,
        completion_rate=round(completed / total * 100, 2) if total else 0.0,
    )
