"""Task business logic with project-scoped access rules."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, UnprocessableEntityError
from app.core.pagination import Page, PageParams
from app.core.timeutils import utcnow
from app.models.enums import TaskPriority, TaskStatus
from app.models.project import ProjectMember
from app.models.task import Task
from app.models.user import User
from app.schemas.task import TaskBulkResult, TaskCreate, TaskRead, TaskUpdate

logger = logging.getLogger(__name__)

_PRIORITY_ORDER = case(
    (Task.priority == TaskPriority.CRITICAL, 3),
    (Task.priority == TaskPriority.HIGH, 2),
    (Task.priority == TaskPriority.MEDIUM, 1),
    else_=0,
)
_STATUS_ORDER = case(
    (Task.status == TaskStatus.BLOCKED, 3),
    (Task.status == TaskStatus.IN_PROGRESS, 2),
    (Task.status == TaskStatus.TODO, 1),
    else_=0,
)


def status_order():  # noqa: ANN201 - SQLAlchemy ordering expression
    """Public ordering expression: blocked first, then in-progress, todo, done.

    Exported so the PDF endpoint can reuse the same deterministic ordering
    without importing a private module member.
    """
    return _STATUS_ORDER


async def _reload(db: AsyncSession, task_id: uuid.UUID) -> Task:
    return (await db.execute(select(Task).where(Task.id == task_id))).scalar_one()


async def _assert_assignee_is_member(
    db: AsyncSession, project_id: uuid.UUID, owner_id: uuid.UUID, assignee_id: uuid.UUID
) -> None:
    if assignee_id == owner_id:
        return
    member = await db.scalar(
        select(ProjectMember.id).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == assignee_id
        )
    )
    if member is None:
        raise UnprocessableEntityError(
            "The assignee must be a member of the project",
            details=[
                {
                    "field": "assignee_id",
                    "message": "User is not a member of this project",
                    "type": "value_error",
                }
            ],
        )


async def get_task_or_404(db: AsyncSession, project_id: uuid.UUID, task_id: uuid.UUID) -> Task:
    task = (
        await db.execute(select(Task).where(Task.id == task_id, Task.project_id == project_id))
    ).scalar_one_or_none()
    if task is None:
        raise NotFoundError("Task not found", code="task_not_found")
    return task


async def list_tasks(
    db: AsyncSession,
    project_id: uuid.UUID,
    params: PageParams,
    *,
    search: str | None = None,
    status: TaskStatus | None = None,
    priority: TaskPriority | None = None,
    assignee_id: uuid.UUID | None = None,
    sort: str = "created_at",
    order: str = "desc",
) -> Page[TaskRead]:
    conditions = [Task.project_id == project_id]
    if search:
        pattern = f"%{search.strip().lower()}%"
        conditions.append(
            or_(func.lower(Task.title).like(pattern), func.lower(Task.description).like(pattern))
        )
    if status is not None:
        conditions.append(Task.status == status)
    if priority is not None:
        conditions.append(Task.priority == priority)
    if assignee_id is not None:
        conditions.append(Task.assignee_id == assignee_id)

    total = await db.scalar(select(func.count()).select_from(Task).where(*conditions)) or 0

    direction = order == "asc"
    sort_columns = {
        "created_at": Task.created_at,
        "updated_at": Task.updated_at,
        "due_date": Task.due_date,
        "title": Task.title,
    }
    if sort == "priority":
        column = _PRIORITY_ORDER
        primary = column.asc() if direction else column.desc()
    elif sort == "status":
        column = _STATUS_ORDER
        primary = column.asc() if direction else column.desc()
    else:
        column = sort_columns.get(sort, Task.created_at)
        primary = column.asc().nulls_last() if direction else column.desc().nulls_last()

    rows = await db.execute(
        select(Task)
        .where(*conditions)
        .order_by(primary, Task.id)
        .offset(params.offset)
        .limit(params.limit)
    )
    items = [TaskRead.model_validate(task) for task in rows.scalars().unique().all()]
    return Page.create(items, total, params)


async def create_task(
    db: AsyncSession, project_id: uuid.UUID, owner_id: uuid.UUID, user: User, data: TaskCreate
) -> TaskRead:
    if data.assignee_id is not None:
        await _assert_assignee_is_member(db, project_id, owner_id, data.assignee_id)

    now = utcnow()
    task = Task(
        project_id=project_id,
        title=data.title,
        description=data.description,
        status=data.status,
        priority=data.priority,
        labels=data.labels,
        assignee_id=data.assignee_id,
        created_by_id=user.id,
        due_date=data.due_date,
        completed_at=now if data.status is TaskStatus.DONE else None,
    )
    db.add(task)
    await db.commit()
    task = await _reload(db, task.id)
    logger.info("Task %s created in project %s", task.id, project_id)
    return TaskRead.model_validate(task)


async def update_task(
    db: AsyncSession,
    project_id: uuid.UUID,
    owner_id: uuid.UUID,
    task_id: uuid.UUID,
    data: TaskUpdate,
) -> TaskRead:
    task = await get_task_or_404(db, project_id, task_id)
    changes = data.model_dump(exclude_unset=True)

    if "assignee_id" in changes and changes["assignee_id"] is not None:
        await _assert_assignee_is_member(db, project_id, owner_id, changes["assignee_id"])

    for field, value in changes.items():
        setattr(task, field, value)

    if "status" in changes:
        if task.status is TaskStatus.DONE and task.completed_at is None:
            task.completed_at = utcnow()
        elif task.status is not TaskStatus.DONE:
            task.completed_at = None

    task.updated_at = utcnow()
    await db.commit()
    task = await _reload(db, task.id)
    return TaskRead.model_validate(task)


async def delete_task(db: AsyncSession, project_id: uuid.UUID, task_id: uuid.UUID) -> None:
    task = await get_task_or_404(db, project_id, task_id)
    await db.delete(task)
    await db.commit()
    logger.info("Task %s deleted from project %s", task_id, project_id)


async def bulk_update_status(
    db: AsyncSession,
    project_id: uuid.UUID,
    task_ids: list[uuid.UUID],
    status: TaskStatus,
) -> TaskBulkResult:
    tasks = list(
        (await db.execute(select(Task).where(Task.project_id == project_id, Task.id.in_(task_ids))))
        .scalars()
        .all()
    )
    if not tasks:
        raise NotFoundError("No matching tasks were found in this project", code="task_not_found")

    now = utcnow()
    for task in tasks:
        task.status = status
        task.completed_at = now if status is TaskStatus.DONE else None
        task.updated_at = now
    await db.commit()
    updated_ids = [task.id for task in tasks]
    logger.info("Bulk status update to %s for %d tasks", status.value, len(updated_ids))
    return TaskBulkResult(updated=len(updated_ids), task_ids=updated_ids)
