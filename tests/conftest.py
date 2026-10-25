"""Pytest fixtures.

Settings and the global database engine are created at import time, so the
test environment must be configured *before* ``app`` is imported. The
per-test database is a throwaway SQLite file; every request gets its own
session through a dependency override, exactly like production (minus the
PostgreSQL server).
"""

from __future__ import annotations

import os

os.environ.update(
    {
        "APP_NAME": "Enterprise API",
        "APP_VERSION": "1.0.0",
        "ENVIRONMENT": "testing",
        "DEBUG": "false",
        "DOCS_ENABLED": "true",
        "SECRET_KEY": "test-secret-key-that-is-long-enough-for-hs256-signing",
        "JWT_ALGORITHM": "HS256",
        "ACCESS_TOKEN_EXPIRE_MINUTES": "30",
        "REFRESH_TOKEN_EXPIRE_DAYS": "7",
        "BCRYPT_ROUNDS": "4",
        # The application-level engine only backs /health/ready; it never stores
        # application data during tests.
        "DATABASE_URL": "sqlite+aiosqlite://",
        "DB_ECHO": "false",
        "AUTO_CREATE_TABLES": "false",
        "CORS_ORIGINS": "http://localhost:3000",
        "TRUSTED_HOSTS": "*",
        "MAX_REQUEST_BODY_BYTES": "1048576",
        "DEFAULT_PAGE_SIZE": "20",
        "MAX_PAGE_SIZE": "100",
        "RATE_LIMIT_ENABLED": "false",
        "AUTH_RATE_LIMIT_PER_MINUTE": "1000",
        "PDF_RATE_LIMIT_PER_MINUTE": "1000",
        "PDF_MAX_TASKS": "500",
        "LOG_LEVEL": "WARNING",
    }
)

from collections.abc import AsyncIterator  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import configure_sqlite_engine  # noqa: E402
from app.db.session import get_db as app_get_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest_asyncio.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    """A dedicated, file-backed SQLite database for one test."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    eng = create_async_engine(
        url,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
    configure_sqlite_engine(eng)
    async with eng.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


@pytest_asyncio.fixture
async def db_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    """HTTP client wired to the real ASGI app with the test database."""

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[app_get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", timeout=30
    ) as http_client:
        yield http_client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def account(client: AsyncClient) -> dict:
    """A registered, authenticated member account."""
    from tests.helpers import register

    return await register(client)
