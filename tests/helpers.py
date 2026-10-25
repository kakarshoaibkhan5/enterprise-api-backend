"""Reusable helpers shared by the integration tests.

The helpers deliberately talk to the public HTTP surface (not service
functions) so the tests exercise routing, dependencies, serialisation, and
error handling exactly as a real client would.
"""

from __future__ import annotations

import uuid
from typing import Any

DEFAULT_PASSWORD = "Str0ngPassw0rd!"


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}@example.com"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def auth_headers(session: dict[str, Any]) -> dict[str, str]:
    return bearer(session["tokens"]["access_token"])


async def register(
    client: Any,
    *,
    email: str | None = None,
    password: str = DEFAULT_PASSWORD,
    full_name: str | None = "Test User",
) -> dict[str, Any]:
    """Register an account and return the ``AuthResponse`` payload."""
    payload: dict[str, Any] = {
        "email": email or unique_email(),
        "password": password,
    }
    if full_name is not None:
        payload["full_name"] = full_name
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def login(client: Any, email: str, password: str = DEFAULT_PASSWORD) -> dict[str, Any]:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


async def create_project(
    client: Any, headers: dict[str, str], *, name: str = "Apollo Migration", **overrides: Any
) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name, **overrides}
    response = await client.post("/api/v1/projects", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def add_member(
    client: Any,
    headers: dict[str, str],
    project_id: str,
    email: str,
    role: str = "viewer",
) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/projects/{project_id}/members",
        json={"email": email, "role": role},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_task(
    client: Any,
    headers: dict[str, str],
    project_id: str,
    *,
    title: str = "Write migration runbook",
    **overrides: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"title": title, **overrides}
    response = await client.post(
        f"/api/v1/projects/{project_id}/tasks", json=payload, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()


def error_code(response: Any) -> str:
    """Return the stable error code from a canonical error envelope."""
    body = response.json()
    assert "error" in body, body
    return body["error"]["code"]


def error_detail_fields(response: Any) -> list[str | None]:
    return [detail.get("field") for detail in response.json()["error"]["details"]]


async def promote_to_admin(session_factory: Any, user_id: str) -> None:
    """Promote an existing account directly in the database.

    There is intentionally no public endpoint for self-promotion; real
    deployments bootstrap the first administrator with ``scripts/seed.py``.
    """
    from app.models.enums import UserRole
    from app.models.user import User

    async with session_factory() as session:
        user = await session.get(User, uuid.UUID(str(user_id)))
        assert user is not None
        user.role = UserRole.ADMIN
        await session.commit()


async def set_user_flag(session_factory: Any, user_id: str, **fields: Any) -> None:
    """Update arbitrary ``User`` columns directly (test-only side channel)."""
    from app.models.user import User

    async with session_factory() as session:
        user = await session.get(User, uuid.UUID(str(user_id)))
        assert user is not None
        for name, value in fields.items():
            setattr(user, name, value)
        await session.commit()
