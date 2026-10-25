"""Administration: access control, user directory, and platform counters."""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from tests.helpers import (
    DEFAULT_PASSWORD,
    auth_headers,
    bearer,
    create_project,
    create_task,
    error_code,
    login,
    promote_to_admin,
    register,
    set_user_flag,
)

ADMIN_USERS = "/api/v1/admin/users"
ADMIN_STATS = "/api/v1/admin/stats"


async def test_admin_endpoints_reject_members(client: AsyncClient) -> None:
    payload = await register(client)
    headers = auth_headers(payload)

    for method, path, kwargs in (
        ("GET", ADMIN_USERS, {}),
        ("GET", ADMIN_STATS, {}),
        ("PATCH", f"{ADMIN_USERS}/{payload['user']['id']}", {"json": {"role": "admin"}}),
    ):
        response = await client.request(method, path, headers=headers, **kwargs)
        assert response.status_code == 403, path
        assert error_code(response) == "admin_required", path

    anonymous = await client.get(ADMIN_USERS)
    assert anonymous.status_code == 401


async def test_admin_lists_and_filters_users(client: AsyncClient, session_factory) -> None:
    admin = await register(client, email="list-admin@example.com", full_name="Admin Person")
    await promote_to_admin(session_factory, admin["user"]["id"])
    await register(client, email="alice@example.com", full_name="Alice Adams")
    await register(client, email="bob@example.com", full_name="Bob Brown")
    carol = await register(client, email="carol@example.com", full_name="Carol Clark")
    await set_user_flag(session_factory, carol["user"]["id"], is_active=False)

    headers = auth_headers(admin)

    everyone = await client.get(ADMIN_USERS, headers=headers)
    assert everyone.status_code == 200
    assert everyone.json()["total"] == 4

    by_email = await client.get(ADMIN_USERS, headers=headers, params={"search": "ALICE"})
    assert [user["email"] for user in by_email.json()["items"]] == ["alice@example.com"]

    by_name = await client.get(ADMIN_USERS, headers=headers, params={"search": "brown"})
    assert [user["email"] for user in by_name.json()["items"]] == ["bob@example.com"]

    by_role = await client.get(ADMIN_USERS, headers=headers, params={"role": "admin"})
    assert [user["email"] for user in by_role.json()["items"]] == ["list-admin@example.com"]

    inactive = await client.get(ADMIN_USERS, headers=headers, params={"is_active": "false"})
    assert [user["email"] for user in inactive.json()["items"]] == ["carol@example.com"]

    active = await client.get(ADMIN_USERS, headers=headers, params={"is_active": "true"})
    assert active.json()["total"] == 3

    page = await client.get(ADMIN_USERS, headers=headers, params={"page": 2, "per_page": 2})
    assert page.json()["page"] == 2
    assert page.json()["pages"] == 2
    assert len(page.json()["items"]) == 2

    # The directory never leaks password material.
    assert all("hashed_password" not in user for user in everyone.json()["items"])

    invalid_role = await client.get(ADMIN_USERS, headers=headers, params={"role": "root"})
    assert invalid_role.status_code == 422


async def test_admin_promotes_user_and_revokes_existing_session(
    client: AsyncClient, session_factory
) -> None:
    admin = await register(client, email="promote-admin@example.com")
    await promote_to_admin(session_factory, admin["user"]["id"])
    target = await register(client, email="promote-target@example.com")

    response = await client.patch(
        f"{ADMIN_USERS}/{target['user']['id']}",
        headers=auth_headers(admin),
        json={"role": "admin"},
    )
    assert response.status_code == 200
    assert response.json()["role"] == "admin"

    # The promotion rotates the target's token version.
    stale = await client.get(ADMIN_STATS, headers=auth_headers(target))
    assert stale.status_code == 401
    assert error_code(stale) == "token_revoked"

    refreshed = await login(client, "promote-target@example.com")
    assert (await client.get(ADMIN_STATS, headers=auth_headers(refreshed))).status_code == 200


async def test_admin_deactivates_and_reactivates_user(client: AsyncClient, session_factory) -> None:
    admin = await register(client, email="flag-admin@example.com")
    await promote_to_admin(session_factory, admin["user"]["id"])
    target = await register(client, email="flag-target@example.com")
    admin_headers = auth_headers(admin)

    deactivated = await client.patch(
        f"{ADMIN_USERS}/{target['user']['id']}",
        headers=admin_headers,
        json={"is_active": False},
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False

    blocked = await client.get("/api/v1/users/me", headers=auth_headers(target))
    assert blocked.status_code == 401
    assert error_code(blocked) == "account_disabled"

    denied_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "flag-target@example.com", "password": DEFAULT_PASSWORD},
    )
    assert denied_login.status_code == 401
    assert error_code(denied_login) == "account_disabled"

    reactivated = await client.patch(
        f"{ADMIN_USERS}/{target['user']['id']}",
        headers=admin_headers,
        json={"is_active": True},
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["is_active"] is True

    # Deactivation rotated the token version, so the pre-existing token stays dead.
    still_stale = await client.get("/api/v1/users/me", headers=auth_headers(target))
    assert still_stale.status_code == 401
    assert error_code(still_stale) == "token_revoked"

    fresh = await login(client, "flag-target@example.com")
    assert (await client.get("/api/v1/users/me", headers=auth_headers(fresh))).status_code == 200


async def test_admin_updates_full_name_without_revoking_session(
    client: AsyncClient, session_factory
) -> None:
    admin = await register(client, email="name-admin@example.com")
    await promote_to_admin(session_factory, admin["user"]["id"])
    target = await register(client, email="name-target@example.com")

    response = await client.patch(
        f"{ADMIN_USERS}/{target['user']['id']}",
        headers=auth_headers(admin),
        json={"full_name": "  Renamed   Person "},
    )
    assert response.status_code == 200
    assert response.json()["full_name"] == "Renamed Person"

    # A profile-only change is not a revocation event.
    assert (await client.get("/api/v1/users/me", headers=auth_headers(target))).status_code == 200


async def test_admin_cannot_lock_themselves_out(client: AsyncClient, session_factory) -> None:
    admin = await register(client, email="self-admin@example.com")
    await promote_to_admin(session_factory, admin["user"]["id"])
    headers = auth_headers(admin)
    path = f"{ADMIN_USERS}/{admin['user']['id']}"

    demote = await client.patch(path, headers=headers, json={"role": "member"})
    assert demote.status_code == 400
    assert error_code(demote) == "self_lockout_prevented"

    deactivate = await client.patch(path, headers=headers, json={"is_active": False})
    assert deactivate.status_code == 400
    assert error_code(deactivate) == "self_lockout_prevented"

    # Re-promoting yourself to admin is a no-op and allowed.
    still_admin = await client.patch(path, headers=headers, json={"role": "admin"})
    assert still_admin.status_code == 200
    assert still_admin.json()["role"] == "admin"


async def test_admin_update_validation_and_missing_user(
    client: AsyncClient, session_factory
) -> None:
    admin = await register(client, email="valid-admin@example.com")
    await promote_to_admin(session_factory, admin["user"]["id"])
    headers = auth_headers(admin)

    missing = await client.patch(
        f"{ADMIN_USERS}/{uuid.uuid4()}", headers=headers, json={"role": "admin"}
    )
    assert missing.status_code == 404
    assert error_code(missing) == "user_not_found"

    invalid_role = await client.patch(
        f"{ADMIN_USERS}/{admin['user']['id']}", headers=headers, json={"role": "root"}
    )
    assert invalid_role.status_code == 422

    invalid_body = await client.patch(
        f"{ADMIN_USERS}/{admin['user']['id']}", headers=headers, json={"is_active": "maybe"}
    )
    assert invalid_body.status_code == 422


async def test_admin_stats_counts_everything(client: AsyncClient, session_factory) -> None:
    admin = await register(client, email="stats-admin@example.com")
    await promote_to_admin(session_factory, admin["user"]["id"])
    owner = await register(client, email="stats-owner@example.com")
    disabled = await register(client, email="stats-disabled@example.com")
    await set_user_flag(session_factory, disabled["user"]["id"], is_active=False)

    project = await create_project(client, auth_headers(owner))
    await create_task(client, auth_headers(owner), project["id"])
    await create_task(client, auth_headers(owner), project["id"])

    response = await client.get(ADMIN_STATS, headers=auth_headers(admin))
    assert response.status_code == 200
    stats = response.json()
    assert stats["total_users"] == 3
    assert stats["active_users"] == 2
    assert stats["admin_users"] == 1
    assert stats["total_projects"] == 1
    assert stats["total_tasks"] == 2


async def test_revoked_admin_token_cannot_be_replayed(client: AsyncClient, session_factory) -> None:
    admin = await register(client, email="replay-admin@example.com")
    await promote_to_admin(session_factory, admin["user"]["id"])
    token = admin["tokens"]["access_token"]

    first = await client.get(ADMIN_STATS, headers=bearer(token))
    assert first.status_code == 200

    # Demote through a second admin session, then confirm the original token dies.
    second_admin = await register(client, email="replay-admin-2@example.com")
    await promote_to_admin(session_factory, second_admin["user"]["id"])
    demoted = await client.patch(
        f"{ADMIN_USERS}/{admin['user']['id']}",
        headers=auth_headers(second_admin),
        json={"role": "member"},
    )
    assert demoted.status_code == 200

    replay = await client.get(ADMIN_STATS, headers=bearer(token))
    assert replay.status_code == 401
    assert error_code(replay) == "token_revoked"
