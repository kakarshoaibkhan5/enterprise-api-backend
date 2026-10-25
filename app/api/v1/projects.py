"""Project, membership, statistics, and PDF report endpoints."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.api.deps import (
    CurrentUser,
    DbSession,
    PageParamsDep,
    ProjectEditor,
    ProjectOwner,
    ProjectViewer,
)
from app.api.errors import COMMON_RESPONSES
from app.core.config import get_settings
from app.core.pagination import Page
from app.core.timeutils import utcnow
from app.models.enums import ProjectStatus, TaskStatus
from app.models.task import Task
from app.schemas.common import MessageResponse
from app.schemas.project import (
    ProjectCreate,
    ProjectMemberCreate,
    ProjectMemberRead,
    ProjectMemberUpdate,
    ProjectRead,
    ProjectStats,
    ProjectUpdate,
)
from app.services import pdf as pdf_service
from app.services import projects as project_service
from app.services.tasks import status_order

settings = get_settings()

router = APIRouter(prefix="/projects", tags=["Projects"], responses=COMMON_RESPONSES)


@router.get(
    "",
    response_model=Page[ProjectRead],
    summary="List projects visible to the caller",
    description=(
        "Admins see every project. Other users see projects they own plus projects "
        "where they hold a membership. Results include the caller's effective role "
        "and aggregate counters."
    ),
)
async def list_projects(
    db: DbSession,
    user: CurrentUser,
    params: PageParamsDep,
    search: str | None = Query(default=None, max_length=120),
    project_status: ProjectStatus | None = Query(default=None, alias="status"),
    sort: Literal["created_at", "updated_at", "name", "due_date", "status"] = Query(
        default="created_at"
    ),
    order: Literal["asc", "desc"] = Query(default="desc"),
) -> Page[ProjectRead]:
    return await project_service.list_projects(
        db,
        user,
        params,
        search=search,
        status=project_status,
        sort=sort,
        order=order,
    )


@router.post(
    "",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project",
    description="The creator becomes the owner and is added as an owner-level member.",
)
async def create_project(
    payload: ProjectCreate,
    db: DbSession,
    user: CurrentUser,
) -> ProjectRead:
    return await project_service.create_project(db, user, payload)


@router.get(
    "/{project_id}",
    response_model=ProjectRead,
    summary="Get a project",
)
async def get_project(db: DbSession, context: ProjectViewer) -> ProjectRead:
    return await project_service.get_project_read(db, context)


@router.patch(
    "/{project_id}",
    response_model=ProjectRead,
    summary="Update a project",
    description="Requires the owner or editor role.",
)
async def update_project(
    payload: ProjectUpdate,
    db: DbSession,
    context: ProjectEditor,
) -> ProjectRead:
    return await project_service.update_project(db, context, payload)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a project",
    description="Requires the owner role. Cascades to members and tasks.",
)
async def delete_project(db: DbSession, context: ProjectOwner) -> Response:
    await project_service.delete_project(db, context)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{project_id}/stats",
    response_model=ProjectStats,
    summary="Project task statistics",
)
async def get_stats(db: DbSession, context: ProjectViewer) -> ProjectStats:
    return await project_service.project_stats(db, context.project.id)


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------
@router.get(
    "/{project_id}/members",
    response_model=list[ProjectMemberRead],
    summary="List project members",
)
async def list_members(db: DbSession, context: ProjectViewer) -> list[ProjectMemberRead]:
    return await project_service.list_members(db, context.project.id)


@router.post(
    "/{project_id}/members",
    response_model=ProjectMemberRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a member by email",
    description="Requires the owner role. Only existing, active users can be added.",
)
async def add_member(
    payload: ProjectMemberCreate,
    db: DbSession,
    context: ProjectOwner,
) -> ProjectMemberRead:
    return await project_service.add_member(db, context, payload)


@router.patch(
    "/{project_id}/members/{user_id}",
    response_model=ProjectMemberRead,
    summary="Change a member's role",
    description="Requires the owner role. The owner's own membership cannot be modified.",
)
async def update_member(
    user_id: uuid.UUID,
    payload: ProjectMemberUpdate,
    db: DbSession,
    context: ProjectOwner,
) -> ProjectMemberRead:
    return await project_service.update_member(db, context, user_id, payload)


@router.delete(
    "/{project_id}/members/{user_id}",
    response_model=MessageResponse,
    summary="Remove a member (or leave a project)",
    description=(
        "Owners may remove anyone except themselves. Members may remove themselves "
        "from a project they do not own."
    ),
)
async def remove_member(
    user_id: uuid.UUID,
    db: DbSession,
    context: ProjectViewer,
) -> MessageResponse:
    await project_service.remove_member(db, context, user_id)
    return MessageResponse(message="Member removed")


# ---------------------------------------------------------------------------
# PDF report
# ---------------------------------------------------------------------------
@router.get(
    "/{project_id}/report.pdf",
    response_class=Response,
    summary="Download a project status report (PDF)",
    description=(
        "Renders a printable PDF containing project metadata, task statistics, the "
        "task list, and team members. Generated in a worker thread and streamed with "
        "a `Content-Disposition` attachment header."
    ),
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "A PDF document",
        },
        404: {"description": "Project not found or caller is not a member"},
    },
)
async def download_report(
    db: DbSession,
    context: ProjectViewer,
    include_completed: bool = Query(
        default=True, description="Include tasks whose status is 'done'"
    ),
) -> Response:
    project = context.project
    conditions = [Task.project_id == project.id]
    if not include_completed:
        conditions.append(Task.status != TaskStatus.DONE)

    limit = settings.pdf_max_tasks
    rows = await db.execute(
        select(Task)
        .where(*conditions)
        .order_by(status_order(), Task.due_date.asc().nulls_last(), Task.created_at)
        .limit(limit + 1)
    )
    tasks = list(rows.scalars().unique().all())
    truncated = len(tasks) > limit
    tasks = tasks[:limit]

    stats = await project_service.project_stats(db, project.id)
    members = await project_service.list_members(db, project.id)

    data = pdf_service.build_report_data(
        project_id=str(project.id),
        project_name=project.name,
        description=project.description,
        status=project.status.value,
        owner_name=project.owner.full_name if project.owner else None,
        owner_email=project.owner.email,
        start_date=project.start_date,
        due_date=project.due_date,
        created_at=project.created_at,
        stats=stats,
        tasks=[
            pdf_service.ReportTask(
                title=task.title,
                status=task.status.value,
                priority=task.priority.value,
                assignee=(task.assignee.full_name or task.assignee.email)
                if task.assignee
                else None,
                due_date=task.due_date,
                labels=list(task.labels or []),
            )
            for task in tasks
        ],
        members=[
            pdf_service.ReportMember(
                name=member.full_name or member.email, email=member.email, role=member.role.value
            )
            for member in members
        ],
        generated_by=context.user.full_name or context.user.email,
        generated_at=utcnow(),
        truncated=truncated,
    )

    content = await run_in_threadpool(pdf_service.render_report, data)
    filename = f"{pdf_service.slugify_filename(project.name)}-report-{date.today():%Y%m%d}.pdf"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(content)),
            "Cache-Control": "no-store",
        },
    )
