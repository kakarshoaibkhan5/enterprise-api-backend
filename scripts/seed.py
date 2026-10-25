"""Seed a database with demo users, projects, and tasks.

The script is idempotent: existing users, projects (matched by owner + name),
memberships, and projects that already contain tasks are left untouched. Run it
after the schema exists::

    alembic upgrade head
    python -m scripts.seed

Credentials are printed at the end. Override the password with ``SEED_PASSWORD``.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.db.base import Base
from app.db.session import AsyncSessionLocal, dispose_engine, engine
from app.models.enums import ProjectRole, ProjectStatus, TaskPriority, TaskStatus, UserRole
from app.models.project import Project, ProjectMember
from app.models.task import Task
from app.models.user import User
from app.schemas.auth import RegisterRequest
from app.schemas.project import ProjectCreate, ProjectMemberCreate
from app.schemas.task import TaskCreate
from app.services.auth import get_user_by_email, register_user
from app.services.projects import add_member, create_project, resolve_project_access
from app.services.tasks import create_task

DEMO_PASSWORD = os.getenv("SEED_PASSWORD", "DemoPassw0rd!")

DEMO_USERS = [
    {
        "email": "ada@example.com",
        "full_name": "Ada Lovelace",
        "role": UserRole.ADMIN,
    },
    {
        "email": "grace@example.com",
        "full_name": "Grace Hopper",
        "role": UserRole.MEMBER,
    },
    {
        "email": "linus@example.com",
        "full_name": "Linus Torvalds",
        "role": UserRole.MEMBER,
    },
]

DEMO_PROJECTS = [
    {
        "name": "Apollo Migration",
        "description": "Move the billing platform to the new event-driven architecture.",
        "status": ProjectStatus.ACTIVE,
        "members": [
            ("grace@example.com", ProjectRole.EDITOR),
            ("linus@example.com", ProjectRole.VIEWER),
        ],
        "tasks": [
            {
                "title": "Inventory legacy billing jobs",
                "description": "Catalogue every scheduled job and its downstream consumer.",
                "status": TaskStatus.DONE,
                "priority": TaskPriority.HIGH,
                "labels": ["discovery", "billing"],
                "assignee": "grace@example.com",
                "due_in": -3,
            },
            {
                "title": "Design the event contract",
                "description": "Publish versioned schemas for invoice and payment events.",
                "status": TaskStatus.IN_PROGRESS,
                "priority": TaskPriority.CRITICAL,
                "labels": ["design", "kafka"],
                "assignee": "ada@example.com",
                "due_in": 4,
            },
            {
                "title": "Backfill historical invoices",
                "description": "Replay 24 months of invoices into the event store.",
                "status": TaskStatus.TODO,
                "priority": TaskPriority.MEDIUM,
                "labels": ["data"],
                "assignee": "grace@example.com",
                "due_in": 12,
            },
            {
                "title": "Write the rollback runbook",
                "description": "Document how to fall back to the monolith within 15 minutes.",
                "status": TaskStatus.BLOCKED,
                "priority": TaskPriority.HIGH,
                "labels": ["operations"],
                "assignee": None,
                "due_in": 7,
            },
        ],
    },
    {
        "name": "Website Refresh",
        "description": "Rebuild the marketing site with a component-driven design system.",
        "status": ProjectStatus.PLANNING,
        "members": [
            ("linus@example.com", ProjectRole.EDITOR),
        ],
        "tasks": [
            {
                "title": "Audit the current information architecture",
                "priority": TaskPriority.MEDIUM,
                "status": TaskStatus.TODO,
                "labels": ["ux"],
                "assignee": "linus@example.com",
                "due_in": 9,
            },
            {
                "title": "Select a typography scale",
                "priority": TaskPriority.LOW,
                "status": TaskStatus.TODO,
                "labels": ["design"],
                "assignee": None,
                "due_in": 14,
            },
        ],
    },
]


async def ensure_user(db, spec: dict) -> tuple[User, bool]:
    user = await get_user_by_email(db, spec["email"])
    if user is not None:
        if spec["role"] is UserRole.ADMIN and user.role is not UserRole.ADMIN:
            user.role = UserRole.ADMIN
            await db.commit()
            await db.refresh(user)
        return user, False

    user = await register_user(
        db,
        RegisterRequest(
            email=spec["email"],
            password=DEMO_PASSWORD,
            full_name=spec["full_name"],
        ),
    )
    if spec["role"] is UserRole.ADMIN:
        user.role = UserRole.ADMIN
        await db.commit()
        await db.refresh(user)
    return user, True


async def ensure_project(db, owner: User, spec: dict) -> tuple[Project, bool]:
    project = await db.scalar(
        select(Project).where(
            Project.owner_id == owner.id,
            func.lower(Project.name) == spec["name"].lower(),
        )
    )
    if project is not None:
        return project, False

    created = await create_project(
        db,
        owner,
        ProjectCreate(
            name=spec["name"],
            description=spec["description"],
            status=spec["status"],
        ),
    )
    project = await db.get(Project, created.id)
    assert project is not None
    return project, True


async def ensure_members(
    db, project: Project, owner: User, members: list[tuple[str, ProjectRole]]
) -> int:
    added = 0
    for email, role in members:
        target = await get_user_by_email(db, email)
        if target is None:  # pragma: no cover - demo data always resolves
            continue
        exists = await db.scalar(
            select(ProjectMember.id).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == target.id,
            )
        )
        if exists is not None:
            continue
        context = await resolve_project_access(db, project.id, owner, ProjectRole.OWNER)
        await add_member(db, context, ProjectMemberCreate(email=target.email, role=role))
        added += 1
    return added


async def ensure_tasks(db, project: Project, owner: User, specs: list[dict]) -> int:
    existing = await db.scalar(
        select(func.count()).select_from(Task).where(Task.project_id == project.id)
    )
    if existing:
        return 0

    created = 0
    for spec in specs:
        assignee = await get_user_by_email(db, spec["assignee"]) if spec["assignee"] else None
        await create_task(
            db,
            project.id,
            project.owner_id,
            owner,
            TaskCreate(
                title=spec["title"],
                description=spec.get("description"),
                status=spec["status"],
                priority=spec["priority"],
                labels=spec.get("labels", []),
                assignee_id=assignee.id if assignee else None,
                due_date=date.today() + timedelta(days=spec["due_in"]),
            ),
        )
        created += 1
    return created


async def main() -> None:
    users_created = projects_created = members_added = tasks_created = 0

    if os.getenv("AUTO_CREATE_TABLES", "false").lower() == "true":
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    try:
        async with AsyncSessionLocal() as db:
            users: dict[str, User] = {}
            for spec in DEMO_USERS:
                user, created = await ensure_user(db, spec)
                users[spec["email"]] = user
                users_created += int(created)

            owner = users["ada@example.com"]
            for spec in DEMO_PROJECTS:
                project, created = await ensure_project(db, owner, spec)
                projects_created += int(created)
                members_added += await ensure_members(db, project, owner, spec["members"])
                tasks_created += await ensure_tasks(db, project, owner, spec["tasks"])
    except OperationalError as exc:
        print("Database schema not found. Run `alembic upgrade head` first.")
        print(f"Underlying error: {exc.orig}")
        raise SystemExit(1) from exc
    finally:
        await dispose_engine()

    print(
        "Seed complete: "
        f"{users_created} users, {projects_created} projects, "
        f"{members_added} memberships, {tasks_created} tasks created."
    )
    print("Demo accounts (all share the same password):")
    for spec in DEMO_USERS:
        print(f"  {spec['email']:<20} {spec['role'].value:<7} {DEMO_PASSWORD}")
    print("Admin account: ada@example.com")


if __name__ == "__main__":
    asyncio.run(main())
