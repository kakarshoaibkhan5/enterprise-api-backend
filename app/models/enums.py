"""Enumerations persisted as portable VARCHAR + CHECK constraints."""

from __future__ import annotations

from enum import Enum, StrEnum
from typing import TypeVar

from sqlalchemy import Enum as SAEnum

E = TypeVar("E", bound=Enum)


def enum_column(enum_cls: type[E], name: str, length: int = 20) -> SAEnum:
    """Build a portable SQLAlchemy Enum.

    ``native_enum=False`` stores values in a VARCHAR and, with
    ``create_constraint=True``, adds a database-level CHECK constraint. This
    keeps migrations simple and behaves identically on PostgreSQL and SQLite.
    """
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        length=length,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda enum: [member.value for member in enum],
    )


class UserRole(StrEnum):
    MEMBER = "member"
    ADMIN = "admin"


class ProjectStatus(StrEnum):
    PLANNING = "planning"
    ACTIVE = "active"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class ProjectRole(StrEnum):
    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"


class TaskStatus(StrEnum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"


class TaskPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


PROJECT_ROLE_RANK: dict[ProjectRole, int] = {
    ProjectRole.VIEWER: 1,
    ProjectRole.EDITOR: 2,
    ProjectRole.OWNER: 3,
}

TASK_STATUS_ORDER: dict[TaskStatus, int] = {
    TaskStatus.TODO: 0,
    TaskStatus.IN_PROGRESS: 1,
    TaskStatus.BLOCKED: 2,
    TaskStatus.DONE: 3,
}

TASK_PRIORITY_ORDER: dict[TaskPriority, int] = {
    TaskPriority.LOW: 0,
    TaskPriority.MEDIUM: 1,
    TaskPriority.HIGH: 2,
    TaskPriority.CRITICAL: 3,
}
