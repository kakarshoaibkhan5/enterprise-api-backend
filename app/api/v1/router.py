"""Versioned API router aggregation."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import admin, auth, projects, tasks, users

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(projects.router)
api_router.include_router(tasks.router)
api_router.include_router(admin.router)

__all__ = ["api_router"]
