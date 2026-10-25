"""Current-user profile endpoints."""

from __future__ import annotations

from httpx import AsyncClient

from tests.helpers import (
    DEFAULT_PASSWORD,
    auth_headers,
    bearer,
    error_code,
    login,
    register,
)

ME = "/api/v1/users/me"


async def test_read_and_update_profile(client: AsyncClient) -> None:
    payload = await register(client, full_name="Original Name")
    headers = auth_headers(payload)

    read = await client.get(ME, headers=headers)
    assert read.status_code == 200
    assert read.json()["full_name"] == "Original Name"

    updated = await client.patch(
        ME,
        headers=headers,
        json={"full_name": "  Grace   Hopper ", "email": "Grace@Example.COM"},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["full_name"] == "Grace Hopper"
    assert body["email"] == "grace@example.com"


async def test_email_change_updates_login_identity(client: AsyncClient) -> None:
    payload = await register(client, email="before@example.com")
    headers = auth_headers(payload)

    response = await client.patch(ME, headers=headers, json={"email": "after@example.com"})
    assert response.status_code == 200

    old = await client.post(
        "/api/v1/auth/login",
        json={"email": "before@example.com", "password": DEFAULT_PASSWORD},
    )
    assert old.status_code == 401

    new = await login(client, "after@example.com")
    assert new["user"]["email"] == "after@example.com"


async def test_email_change_rejects_duplicate(client: AsyncClient) -> None:
    await register(client, email="taken@example.com")
    second = await register(client, email="second@example.com")

    response = await client.patch(
        ME, headers=auth_headers(second), json={"email": "TAKEN@example.com"}
    )
    assert response.status_code == 409
    assert error_code(response) == "email_already_registered"


async def test_profile_validation_and_authentication(client: AsyncClient) -> None:
    payload = await register(client)

    unauth = await client.patch(ME, json={"full_name": "Nobody"})
    assert unauth.status_code == 401

    too_long = await client.patch(ME, headers=auth_headers(payload), json={"full_name": "x" * 121})
    assert too_long.status_code == 422
    assert error_code(too_long) == "validation_error"

    bad_email = await client.patch(ME, headers=auth_headers(payload), json={"email": "nope"})
    assert bad_email.status_code == 422


async def test_clear_full_name(client: AsyncClient) -> None:
    payload = await register(client, full_name="Temporary")
    response = await client.patch(ME, headers=auth_headers(payload), json={"full_name": "   "})

    assert response.status_code == 200
    assert response.json()["full_name"] is None


async def test_delete_account_requires_confirmation_and_password(
    client: AsyncClient,
) -> None:
    payload = await register(client, email="delete@example.com")
    headers = auth_headers(payload)

    unconfirmed = await client.request(
        "DELETE",
        ME,
        headers=headers,
        json={"password": DEFAULT_PASSWORD, "confirm": False},
    )
    assert unconfirmed.status_code == 400
    assert error_code(unconfirmed) == "not_confirmed"

    wrong = await client.request(
        "DELETE",
        ME,
        headers=headers,
        json={"password": "WrongPass1", "confirm": True},
    )
    assert wrong.status_code == 400
    assert error_code(wrong) == "invalid_password"

    deleted = await client.request(
        "DELETE",
        ME,
        headers=headers,
        json={"password": DEFAULT_PASSWORD, "confirm": True},
    )
    assert deleted.status_code == 200
    assert deleted.json()["message"] == "Account deleted"

    # The account and its token are gone.
    assert (await client.get(ME, headers=headers)).status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "delete@example.com", "password": DEFAULT_PASSWORD},
        )
    ).status_code == 401


async def test_deleting_account_cascades_to_owned_projects(
    client: AsyncClient, session_factory
) -> None:
    from sqlalchemy import func, select

    from app.models.project import Project
    from tests.helpers import create_project

    payload = await register(client)
    await create_project(client, auth_headers(payload), name="Doomed Project")

    response = await client.request(
        "DELETE",
        ME,
        headers=auth_headers(payload),
        json={"password": DEFAULT_PASSWORD, "confirm": True},
    )
    assert response.status_code == 200

    async with session_factory() as session:
        remaining = await session.scalar(select(func.count()).select_from(Project))
    assert remaining == 0


async def test_deleted_account_token_is_rejected(client: AsyncClient) -> None:
    payload = await register(client)
    token = payload["tokens"]["access_token"]

    deleted = await client.request(
        "DELETE",
        ME,
        headers=bearer(token),
        json={"password": DEFAULT_PASSWORD, "confirm": True},
    )
    assert deleted.status_code == 200

    response = await client.get(ME, headers=bearer(token))
    assert response.status_code == 401
    assert error_code(response) == "invalid_token"
