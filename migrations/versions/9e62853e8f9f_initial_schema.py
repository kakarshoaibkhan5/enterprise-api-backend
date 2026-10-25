"""initial schema

Revision ID: 9e62853e8f9f
Revises:
Create Date: 2026-10-25 08:32:16.979405

Creates the five core tables: users, projects, refresh_tokens,
project_members, and tasks.

The DDL is deliberately dialect-neutral so the same migration runs unchanged on
PostgreSQL (production) and SQLite (tests and local development):

* timestamps use the standard ``CURRENT_TIMESTAMP`` keyword;
* enums are stored as ``VARCHAR`` plus a ``CHECK`` constraint
  (``native_enum=False``), avoiding PostgreSQL ``CREATE TYPE`` migrations;
* JSON columns use ``JSONB`` on PostgreSQL and ``JSON`` elsewhere.

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9e62853e8f9f"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Standard SQL, understood by PostgreSQL and SQLite alike.
NOW = sa.text("CURRENT_TIMESTAMP")

# JSONB on PostgreSQL, plain JSON on every other backend.
LABELS = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

USER_ROLE = sa.Enum(
    "member",
    "admin",
    name="user_role",
    native_enum=False,
    create_constraint=True,
    length=10,
)
PROJECT_STATUS = sa.Enum(
    "planning",
    "active",
    "on_hold",
    "completed",
    "archived",
    name="project_status",
    native_enum=False,
    create_constraint=True,
    length=20,
)
PROJECT_ROLE = sa.Enum(
    "owner",
    "editor",
    "viewer",
    name="project_role",
    native_enum=False,
    create_constraint=True,
    length=10,
)
TASK_STATUS = sa.Enum(
    "todo",
    "in_progress",
    "blocked",
    "done",
    name="task_status",
    native_enum=False,
    create_constraint=True,
    length=20,
)
TASK_PRIORITY = sa.Enum(
    "low",
    "medium",
    "high",
    "critical",
    name="task_priority",
    native_enum=False,
    create_constraint=True,
    length=10,
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=True),
        sa.Column("role", USER_ROLE, server_default="member", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("token_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_role", "users", ["role"], unique=False)

    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", PROJECT_STATUS, server_default="active", nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"], unique=False)
    op.create_index("ix_projects_status", "projects", ["status"], unique=False)
    op.create_index(
        "ix_projects_owner_created", "projects", ["owner_id", "created_at"], unique=False
    )

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"], unique=False)
    op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True)

    op.create_table(
        "project_members",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", PROJECT_ROLE, server_default="viewer", nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_members_project_user"),
    )
    op.create_index(
        "ix_project_members_project_id", "project_members", ["project_id"], unique=False
    )
    op.create_index("ix_project_members_user_id", "project_members", ["user_id"], unique=False)

    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", TASK_STATUS, server_default="todo", nullable=False),
        sa.Column("priority", TASK_PRIORITY, server_default="medium", nullable=False),
        sa.Column("labels", LABELS, nullable=False),
        sa.Column("assignee_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(["assignee_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_project_id", "tasks", ["project_id"], unique=False)
    op.create_index("ix_tasks_project_status", "tasks", ["project_id", "status"], unique=False)
    op.create_index("ix_tasks_project_priority", "tasks", ["project_id", "priority"], unique=False)
    op.create_index("ix_tasks_project_created", "tasks", ["project_id", "created_at"], unique=False)
    op.create_index("ix_tasks_assignee_id", "tasks", ["assignee_id"], unique=False)
    op.create_index("ix_tasks_project_due_date", "tasks", ["project_id", "due_date"], unique=False)


def downgrade() -> None:
    # Dropping a table also drops its indexes and constraints on both supported
    # backends, so the reverse-order table drops are sufficient.
    op.drop_table("tasks")
    op.drop_table("project_members")
    op.drop_table("refresh_tokens")
    op.drop_table("projects")
    op.drop_table("users")
