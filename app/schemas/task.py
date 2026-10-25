"""Task schemas."""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import TaskPriority, TaskStatus
from app.schemas.auth import UserSummary
from app.schemas.common import UTCDatetime

MAX_LABELS = 20
MAX_LABEL_LENGTH = 40


def _clean_labels(labels: list[str] | None) -> list[str] | None:
    if labels is None:
        return None
    cleaned: list[str] = []
    seen: set[str] = set()
    for label in labels:
        value = " ".join(label.split())
        if not value:
            raise ValueError("Labels may not be blank")
        if len(value) > MAX_LABEL_LENGTH:
            raise ValueError(f"Each label must be at most {MAX_LABEL_LENGTH} characters")
        key = value.lower()
        if key not in seen:
            seen.add(key)
            cleaned.append(value)
    if len(cleaned) > MAX_LABELS:
        raise ValueError(f"A task may have at most {MAX_LABELS} labels")
    return cleaned


class TaskBase(BaseModel):
    title: str = Field(min_length=1, max_length=200, examples=["Write migration runbook"])
    description: str | None = Field(default=None, max_length=10_000)
    status: TaskStatus = TaskStatus.TODO
    priority: TaskPriority = TaskPriority.MEDIUM
    labels: list[str] = Field(default_factory=list, max_length=MAX_LABELS)
    assignee_id: uuid.UUID | None = None
    due_date: date | None = None

    @field_validator("title")
    @classmethod
    def _clean_title(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Task title may not be blank")
        return cleaned

    @field_validator("description")
    @classmethod
    def _clean_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("labels")
    @classmethod
    def _validate_labels(cls, value: list[str]) -> list[str]:
        return _clean_labels(value) or []


class TaskCreate(TaskBase):
    pass


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    labels: list[str] | None = Field(default=None, max_length=MAX_LABELS)
    assignee_id: uuid.UUID | None = None
    due_date: date | None = None

    @field_validator("title")
    @classmethod
    def _clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Task title may not be blank")
        return cleaned

    @field_validator("labels")
    @classmethod
    def _validate_labels(cls, value: list[str] | None) -> list[str] | None:
        return _clean_labels(value)


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    description: str | None = None
    status: TaskStatus
    priority: TaskPriority
    labels: list[str]
    assignee: UserSummary | None = None
    created_by: UserSummary | None = None
    due_date: date | None = None
    completed_at: UTCDatetime | None = None
    created_at: UTCDatetime
    updated_at: UTCDatetime


class TaskBulkStatusUpdate(BaseModel):
    task_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    status: TaskStatus


class TaskBulkResult(BaseModel):
    updated: int
    task_ids: list[uuid.UUID]
