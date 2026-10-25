"""Declarative base and shared column mixins."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.timeutils import utcnow

# ``CURRENT_TIMESTAMP`` is standard SQL and is accepted by both PostgreSQL
# (as ``timestamptz``) and SQLite. Using a literal instead of ``func.now()``
# keeps the generated DDL identical across dialects, so one migration works
# everywhere and autogenerate produces no spurious diffs.
_CURRENT_TIMESTAMP = text("CURRENT_TIMESTAMP")


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model."""


class TimestampMixin:
    """``created_at`` / ``updated_at`` columns with UTC defaults."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        server_default=_CURRENT_TIMESTAMP,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        server_default=_CURRENT_TIMESTAMP,
        nullable=False,
    )
