"""Project and member schemas."""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.models.enums import ProjectRole, ProjectStatus
from app.schemas.common import UTCDatetime


class ProjectBase(BaseModel):
    name: str = Field(min_length=1, max_length=120, examples=["Apollo Migration"])
    description: str | None = Field(default=None, max_length=5000)
    status: ProjectStatus = ProjectStatus.ACTIVE
    start_date: date | None = None
    due_date: date | None = None

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Project name may not be blank")
        return cleaned

    @field_validator("description")
    @classmethod
    def _clean_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def _check_dates(self) -> ProjectBase:
        if self.start_date and self.due_date and self.due_date < self.start_date:
            raise ValueError("due_date must be on or after start_date")
        return self


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    status: ProjectStatus | None = None
    start_date: date | None = None
    due_date: date | None = None

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Project name may not be blank")
        return cleaned


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None = None
    status: ProjectStatus
    start_date: date | None = None
    due_date: date | None = None
    owner_id: uuid.UUID
    owner_email: EmailStr | None = None
    created_at: UTCDatetime
    updated_at: UTCDatetime
    # Contextual fields computed for the requesting user.
    role: ProjectRole | None = Field(
        default=None, description="The requesting user's role on this project"
    )
    member_count: int = Field(default=0, description="Number of members, including the owner")
    task_count: int = Field(default=0, description="Number of tasks in the project")


class ProjectMemberCreate(BaseModel):
    email: EmailStr = Field(description="Email of an existing user to add to the project")
    role: ProjectRole = Field(
        default=ProjectRole.VIEWER,
        description="Membership role. Ownership cannot be granted via invitations.",
    )

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()

    @field_validator("role")
    @classmethod
    def _forbid_owner(cls, value: ProjectRole) -> ProjectRole:
        if value is ProjectRole.OWNER:
            raise ValueError("Ownership is transferred, not granted; use 'editor' or 'viewer'")
        return value


class ProjectMemberUpdate(BaseModel):
    role: ProjectRole = Field(description="New membership role (editor or viewer)")

    @field_validator("role")
    @classmethod
    def _forbid_owner(cls, value: ProjectRole) -> ProjectRole:
        if value is ProjectRole.OWNER:
            raise ValueError("Ownership cannot be granted; use 'editor' or 'viewer'")
        return value


class ProjectMemberRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    user_id: uuid.UUID
    email: EmailStr
    full_name: str | None = None
    role: ProjectRole
    joined_at: UTCDatetime


class ProjectStats(BaseModel):
    project_id: uuid.UUID
    total_tasks: int
    by_status: dict[str, int]
    by_priority: dict[str, int]
    completed_tasks: int
    overdue_tasks: int
    completion_rate: float = Field(description="Percentage of tasks in the 'done' state")
