"""Authentication flows: registration, login, rotation, revocation."""

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

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
TOKEN = "/api/v1/auth/token"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
LOGOUT_ALL = "/api/v1/auth/logout-all"
ME = "/api/v1/auth/me"
USERS_ME = "/api/v1/users/me"


async def test_register_returns_user_and_token_pair(client: AsyncClient) -> None:
    payload = await register(client, email="Ada@Example.COM", full_name="  Ada   Lovelace ")
    user, tokens = payload["user"], payload["tokens"]

    assert user["email"] == "ada@example.com"
    assert user["full_name"] == "Ada Lovelace"
    assert user["role"] == "member"
    assert user["is_active"] is True
    assert user["last_login_at"] is None
    assert "hashed_password" not in user
    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] == 30 * 60
    assert tokens["refresh_expires_in"] == 7 * 86_400
    assert tokens["access_token"] != tokens["refresh_token"]

    me = await client.get(ME, headers=auth_headers(payload))
    assert me.status_code == 200
    assert me.json()["id"] == user["id"]


async def test_register_rejects_duplicate_email_case_insensitively(
    client: AsyncClient,
) -> None:
    await register(client, email="duplicate@example.com")
    response = await client.post(
        REGISTER, json={"email": "DUPLICATE@example.com", "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 409
    assert error_code(response) == "email_already_registered"


async def test_register_validates_password_strength(client: AsyncClient) -> None:
    for password in ("short1", "onlyletters", "12345678"):
        response = await client.post(
            REGISTER, json={"email": f"weak-{password}@example.com", "password": password}
        )
        assert response.status_code == 422, password
        assert error_code(response) == "validation_error"


async def test_register_rejects_invalid_email(client: AsyncClient) -> None:
    response = await client.post(
        REGISTER, json={"email": "not-an-email", "password": DEFAULT_PASSWORD}
    )
    assert response.status_code == 422
    fields = {detail["field"] for detail in response.json()["error"]["details"]}
    assert "email" in fields


async def test_login_records_last_login_and_normalises_email(client: AsyncClient) -> None:
    registration = await register(client, email="login@example.com")
    body = await login(client, "LOGIN@example.com")

    assert body["user"]["id"] == registration["user"]["id"]
    assert body["user"]["last_login_at"] is not None
    assert body["tokens"]["access_token"]


async def test_login_rejects_bad_credentials(client: AsyncClient) -> None:
    await register(client, email="wrong-password@example.com")

    wrong = await client.post(
        LOGIN, json={"email": "wrong-password@example.com", "password": "Nope12345"}
    )
    assert wrong.status_code == 401
    assert error_code(wrong) == "invalid_credentials"

    unknown = await client.post(
        LOGIN, json={"email": "ghost@example.com", "password": DEFAULT_PASSWORD}
    )
    assert unknown.status_code == 401
    assert error_code(unknown) == "invalid_credentials"


async def test_oauth2_password_form_issues_tokens(client: AsyncClient) -> None:
    await register(client, email="swagger@example.com")
    response = await client.post(
        TOKEN,
        data={"username": "swagger@example.com", "password": DEFAULT_PASSWORD},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert "user" not in body


async def test_missing_and_invalid_tokens_are_rejected(client: AsyncClient) -> None:
    anonymous = await client.get(ME)
    assert anonymous.status_code == 401
    assert error_code(anonymous) == "authentication_required"
    assert anonymous.headers["www-authenticate"] == "Bearer"

    invalid = await client.get(ME, headers=bearer("not-a-real-token"))
    assert invalid.status_code == 401
    assert error_code(invalid) == "invalid_token"


async def test_refresh_token_cannot_be_used_as_access_token(client: AsyncClient) -> None:
    payload = await register(client)
    response = await client.get(ME, headers=bearer(payload["tokens"]["refresh_token"]))
    assert response.status_code == 401
    assert error_code(response) == "invalid_token"


async def test_access_token_cannot_be_used_to_refresh(client: AsyncClient) -> None:
    payload = await register(client)
    response = await client.post(REFRESH, json={"refresh_token": payload["tokens"]["access_token"]})
    assert response.status_code == 401
    assert error_code(response) == "invalid_refresh_token"


async def test_refresh_rotation_issues_new_pair(client: AsyncClient) -> None:
    payload = await register(client)
    old_refresh = payload["tokens"]["refresh_token"]
    old_access = payload["tokens"]["access_token"]

    rotated = await client.post(REFRESH, json={"refresh_token": old_refresh})
    assert rotated.status_code == 200
    new_tokens = rotated.json()["tokens"]
    assert new_tokens["refresh_token"] != old_refresh
    assert new_tokens["access_token"] != old_access

    # The freshly minted access token still works.
    assert (
        await client.get(USERS_ME, headers=bearer(new_tokens["access_token"]))
    ).status_code == 200


async def test_refresh_token_reuse_terminates_every_session(client: AsyncClient) -> None:
    payload = await register(client)
    old_refresh = payload["tokens"]["refresh_token"]

    rotated = await client.post(REFRESH, json={"refresh_token": old_refresh})
    new_tokens = rotated.json()["tokens"]

    replay = await client.post(REFRESH, json={"refresh_token": old_refresh})
    assert replay.status_code == 401
    assert error_code(replay) == "refresh_token_reuse"

    # Reuse detection bumped the token version, so both access tokens die.
    for token in (payload["tokens"]["access_token"], new_tokens["access_token"]):
        response = await client.get(USERS_ME, headers=bearer(token))
        assert response.status_code == 401
        assert error_code(response) == "token_revoked"


async def test_logout_with_specific_token_leaves_other_sessions(
    client: AsyncClient,
) -> None:
    first = await register(client)
    second = await login(client, first["user"]["email"])

    response = await client.post(
        LOGOUT,
        headers=auth_headers(first),
        json={"refresh_token": first["tokens"]["refresh_token"]},
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Logged out"

    # The second session is untouched.
    assert (await client.get(USERS_ME, headers=auth_headers(second))).status_code == 200

    # Replaying the revoked token is treated as theft and kills everything.
    replay = await client.post(REFRESH, json={"refresh_token": first["tokens"]["refresh_token"]})
    assert replay.status_code == 401
    assert error_code(replay) == "refresh_token_reuse"
    revoked = await client.get(USERS_ME, headers=auth_headers(second))
    assert error_code(revoked) == "token_revoked"


async def test_logout_without_body_revokes_all_refresh_tokens(
    client: AsyncClient,
) -> None:
    payload = await register(client)
    response = await client.post(LOGOUT, headers=auth_headers(payload))
    assert response.status_code == 200

    # Access tokens are not revoked by a plain logout.
    assert (await client.get(USERS_ME, headers=auth_headers(payload))).status_code == 200

    refresh = await client.post(REFRESH, json={"refresh_token": payload["tokens"]["refresh_token"]})
    assert refresh.status_code == 401
    assert error_code(refresh) == "refresh_token_reuse"


async def test_logout_all_invalidates_access_tokens_immediately(
    client: AsyncClient,
) -> None:
    payload = await register(client)
    response = await client.post(LOGOUT_ALL, headers=auth_headers(payload))
    assert response.status_code == 200
    assert response.json()["message"] == "All sessions have been terminated"

    rejected = await client.get(USERS_ME, headers=auth_headers(payload))
    assert rejected.status_code == 401
    assert error_code(rejected) == "token_revoked"


async def test_change_password_revokes_sessions_and_updates_credentials(
    client: AsyncClient,
) -> None:
    payload = await register(client, email="rotate@example.com")
    old_refresh = payload["tokens"]["refresh_token"]

    wrong = await client.put(
        f"{USERS_ME}/password",
        headers=auth_headers(payload),
        json={"current_password": "WrongPass1", "new_password": "BrandNewPass2"},
    )
    assert wrong.status_code == 400
    assert error_code(wrong) == "invalid_password"

    unchanged = await client.put(
        f"{USERS_ME}/password",
        headers=auth_headers(payload),
        json={"current_password": DEFAULT_PASSWORD, "new_password": DEFAULT_PASSWORD},
    )
    assert unchanged.status_code == 400
    assert error_code(unchanged) == "password_unchanged"

    changed = await client.put(
        f"{USERS_ME}/password",
        headers=auth_headers(payload),
        json={"current_password": DEFAULT_PASSWORD, "new_password": "BrandNewPass2"},
    )
    assert changed.status_code == 200

    revoked = await client.get(USERS_ME, headers=auth_headers(payload))
    assert error_code(revoked) == "token_revoked"

    old_refresh_attempt = await client.post(REFRESH, json={"refresh_token": old_refresh})
    assert old_refresh_attempt.status_code == 401

    old_login = await client.post(
        LOGIN, json={"email": "rotate@example.com", "password": DEFAULT_PASSWORD}
    )
    assert old_login.status_code == 401

    new_login = await client.post(
        LOGIN, json={"email": "rotate@example.com", "password": "BrandNewPass2"}
    )
    assert new_login.status_code == 200


async def test_disabled_account_cannot_log_in_or_use_access_token(
    client: AsyncClient, session_factory
) -> None:
    from tests.helpers import set_user_flag

    payload = await register(client)
    await set_user_flag(session_factory, payload["user"]["id"], is_active=False)

    blocked = await client.get(USERS_ME, headers=auth_headers(payload))
    assert blocked.status_code == 401
    assert error_code(blocked) == "account_disabled"

    denied = await client.post(
        LOGIN,
        json={"email": payload["user"]["email"], "password": DEFAULT_PASSWORD},
    )
    assert denied.status_code == 401
    assert error_code(denied) == "account_disabled"


async def test_refresh_for_disabled_account_is_rejected(
    client: AsyncClient, session_factory
) -> None:
    from tests.helpers import set_user_flag

    payload = await register(client)
    await set_user_flag(session_factory, payload["user"]["id"], is_active=False)

    response = await client.post(
        REFRESH, json={"refresh_token": payload["tokens"]["refresh_token"]}
    )
    assert response.status_code == 401
    assert error_code(response) == "account_disabled"
