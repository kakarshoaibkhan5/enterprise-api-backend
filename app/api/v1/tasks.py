"""Task endpoints, nested under a project."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Query, Response, status

from app.api.deps import DbSession, PageParamsDep, ProjectEditor, ProjectViewer
from app.api.errors import COMMON_RESPONSES
from app.core.pagination import Page
from app.models.enums import TaskPriority, TaskStatus
from app.schemas.task import (
    TaskBulkResult,
    TaskBulkStatusUpdate,
    TaskCreate,
    TaskRead,
    TaskUpdate,
)
from app.services import tasks as task_service

router = APIRouter(
    prefix="/projects/{project_id}/tasks",
    tags=["Tasks"],
    responses=COMMON_RESPONSES,
)


@router.get(
    "",
    response_model=Page[TaskRead],
    summary="List tasks in a project",
    description=(
        "Filter by status, priority, assignee, or a free-text fragment of the title "
        "or description. Sort by creation date, due date, title, priority, or status."
    ),
)
async def list_tasks(
    db: DbSession,
    context: ProjectViewer,
    params: PageParamsDep,
    search: str | None = Query(default=None, max_length=120),
    task_status: TaskStatus | None = Query(default=None, alias="status"),
    priority: TaskPriority | None = Query(default=None),
    assignee_id: uuid.UUID | None = Query(default=None),
    sort: Literal["created_at", "updated_at", "due_date", "title", "priority", "status"] = Query(
        default="created_at"
    ),
    order: Literal["asc", "desc"] = Query(default="desc"),
) -> Page[TaskRead]:
    return await task_service.list_tasks(
        db,
        context.project.id,
        params,
        search=search,
        status=task_status,
        priority=priority,
        assignee_id=assignee_id,
        sort=sort,
        order=order,
    )


@router.post(
    "",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a task",
    description=(
        "Requires the owner or editor role. If an assignee is supplied they must "
        "already be a member of the project."
    ),
)
async def create_task(
    payload: TaskCreate,
    db: DbSession,
    context: ProjectEditor,
) -> TaskRead:
    return await task_service.create_task(
        db, context.project.id, context.project.owner_id, context.user, payload
    )


@router.post(
    "/bulk-status",
    response_model=TaskBulkResult,
    summary="Update the status of many tasks at once",
    description=(
        "Applies one status to up to 100 tasks in a single transaction. Ids that do "
        "not belong to this project are ignored; the response lists the ids updated."
    ),
)
async def bulk_update_status(
    payload: TaskBulkStatusUpdate,
    db: DbSession,
    context: ProjectEditor,
) -> TaskBulkResult:
    return await task_service.bulk_update_status(
        db, context.project.id, payload.task_ids, payload.status
    )


@router.get(
    "/{task_id}",
    response_model=TaskRead,
    summary="Get a task",
)
async def get_task(
    task_id: uuid.UUID,
    db: DbSession,
    context: ProjectViewer,
) -> TaskRead:
    task = await task_service.get_task_or_404(db, context.project.id, task_id)
    return TaskRead.model_validate(task)


@router.patch(
    "/{task_id}",
    response_model=TaskRead,
    summary="Update a task",
    description=(
        "Requires the owner or editor role. Moving a task into or out of the 'done' "
        "state maintains its completion timestamp automatically."
    ),
)
async def update_task(
    task_id: uuid.UUID,
    payload: TaskUpdate,
    db: DbSession,
    context: ProjectEditor,
) -> TaskRead:
    return await task_service.update_task(
        db, context.project.id, context.project.owner_id, task_id, payload
    )


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a task",
    description="Requires the owner or editor role.",
)
async def delete_task(
    task_id: uuid.UUID,
    db: DbSession,
    context: ProjectEditor,
) -> Response:
    await task_service.delete_task(db, context.project.id, task_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
