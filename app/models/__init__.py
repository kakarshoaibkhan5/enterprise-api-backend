"""Import every model so SQLAlchemy's registry and Alembic see all tables."""

from app.models.enums import (
    ProjectRole,
    ProjectStatus,
    TaskPriority,
    TaskStatus,
    UserRole,
)
from app.models.project import Project, ProjectMember
from app.models.refresh_token import RefreshToken
from app.models.task import Task
from app.models.user import User

__all__ = [
    "Project",
    "ProjectMember",
    "ProjectRole",
    "ProjectStatus",
    "RefreshToken",
    "Task",
    "TaskPriority",
    "TaskStatus",
    "User",
    "UserRole",
]
