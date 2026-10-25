"""Health and readiness probes for load balancers and orchestrators."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response, status
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.db.session import ping_database
from app.schemas.common import HealthResponse, LivenessResponse

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/health", tags=["Health"])


@router.get(
    "/live",
    response_model=LivenessResponse,
    summary="Liveness probe",
    description=(
        "Returns 200 while the process is running. Does not touch the database, so "
        "a failing database never causes the orchestrator to restart the container."
    ),
)
async def liveness() -> LivenessResponse:
    return LivenessResponse()


@router.get(
    "/ready",
    response_model=HealthResponse,
    summary="Readiness probe",
    description=(
        "Checks database connectivity. Returns 503 with `status=degraded` when the "
        "database is unreachable so the instance is removed from the load balancer."
    ),
    responses={503: {"model": HealthResponse, "description": "Dependency unavailable"}},
)
async def readiness() -> Response:
    database = "up"
    try:
        await ping_database()
    except Exception as exc:  # noqa: BLE001 - report any connectivity failure
        logger.error("Readiness check failed: %s", exc)
        database = "down"

    payload = HealthResponse(
        status="ok" if database == "up" else "degraded",
        version=settings.app_version,
        environment=settings.environment,
        database=database,
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK if database == "up" else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload.model_dump(mode="json"),
    )


@router.get(
    "",
    response_model=HealthResponse,
    summary="Service health",
    description="Alias for `/health/ready`, convenient for simple monitoring checks.",
    responses={503: {"model": HealthResponse, "description": "Dependency unavailable"}},
)
async def health() -> Response:
    return await readiness()
