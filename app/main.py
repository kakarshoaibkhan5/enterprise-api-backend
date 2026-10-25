"""Enterprise API - FastAPI application factory and lifecycle.

Run locally with::

    uvicorn app.main:app --reload

Interactive documentation is served at ``/docs`` (Swagger UI) and ``/redoc``
(ReDoc); the raw schema is available at ``/openapi.json``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

import app.models  # noqa: F401 - register every ORM model before create_all
from app.api.errors import register_exception_handlers
from app.api.middleware import (
    BodySizeLimitMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.api.v1.health import router as health_router
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.base import Base
from app.db.session import dispose_engine, engine

settings = get_settings()
logger = logging.getLogger(__name__)

DESCRIPTION = """
A production-ready REST API for project and task management, built with
FastAPI, async SQLAlchemy, and PostgreSQL.

### Features

* **JWT authentication** with rotating, single-use refresh tokens and immediate
  session revocation.
* **Multi-tenant projects** with owner / editor / viewer roles. Non-members
  receive `404` so project existence is never leaked.
* **Tasks** with status, priority, labels, assignees, due dates, filtering,
  sorting, pagination, and bulk status updates.
* **PDF reporting** rendered server-side and streamed as a download.
* **Consistent error envelope** on every failure, including a `request_id` that
  matches the `X-Request-ID` response header and the server logs.

### Authentication

1. `POST /api/v1/auth/register` or `POST /api/v1/auth/login`.
2. Copy `tokens.access_token` into the **Authorize** dialog as a bearer token,
   or send `Authorization: Bearer <token>` yourself.
3. Refresh with `POST /api/v1/auth/refresh` before the access token expires.
"""

TAGS_METADATA = [
    {"name": "Authentication", "description": "Registration, login, token rotation, logout."},
    {"name": "Users", "description": "The authenticated user's own profile and account."},
    {"name": "Projects", "description": "Projects, membership, statistics, and PDF reports."},
    {"name": "Tasks", "description": "Tasks scoped to a project."},
    {"name": "Administration", "description": "Platform administration (admin role only)."},
    {"name": "Health", "description": "Liveness and readiness probes."},
]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging(settings.log_level)
    logger.info(
        "Starting %s %s (environment=%s)",
        settings.app_name,
        settings.app_version,
        settings.environment,
    )

    if settings.auto_create_tables:
        if settings.is_production:  # pragma: no cover - guarded by config validation
            raise RuntimeError("Refusing to auto-create tables in production")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        logger.info("Database schema ensured via metadata.create_all()")

    try:
        yield
    finally:
        await dispose_engine()
        logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    docs_enabled = settings.docs_enabled
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=DESCRIPTION,
        summary="Projects, tasks, and PDF reports with JWT authentication",
        openapi_tags=TAGS_METADATA,
        contact={"name": "API maintainers", "email": "maintainers@example.com"},
        license_info={"name": "MIT", "url": "https://opensource.org/licenses/MIT"},
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=lifespan,
        swagger_ui_parameters={"persistAuthorization": True, "displayRequestDuration": True},
    )

    register_exception_handlers(application)

    # Middleware is applied bottom-up: the last added is the outermost layer.
    application.add_middleware(
        RateLimitMiddleware,
    )
    application.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)
    # RequestContext sits *inside* SecurityHeaders so that the 500 response it
    # synthesises for unhandled errors still carries the hardening headers.
    application.add_middleware(RequestContextMiddleware)
    application.add_middleware(SecurityHeadersMiddleware)
    if settings.trusted_hosts != ["*"]:
        application.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Retry-After", "Content-Disposition"],
        max_age=600,
    )

    application.include_router(health_router)
    application.include_router(api_router, prefix=settings.api_v1_prefix)

    @application.get("/", include_in_schema=False)
    async def root() -> JSONResponse:
        payload = {
            "name": settings.app_name,
            "version": settings.app_version,
            "environment": settings.environment,
            "docs": "/docs" if docs_enabled else None,
            "openapi": "/openapi.json" if docs_enabled else None,
            "health": "/health/ready",
            "api": settings.api_v1_prefix,
        }
        return JSONResponse(payload)

    return application


app = create_app()
