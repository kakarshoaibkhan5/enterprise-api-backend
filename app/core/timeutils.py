"""Timezone helpers used across the application.

SQLite does not preserve timezone information, so every datetime read from the
database is normalised back to an aware UTC datetime at the schema boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """Attach UTC to naive datetimes and convert aware ones to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
