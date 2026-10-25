"""Projects: CRUD, tenancy isolation, members, roles, and statistics."""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from tests.helpers import (
    add_member,
    auth_headers,
    create_project,
    create_task,
    error_code,
    promote_to_admin,
    register,
    set_user_flag,
)

PROJECTS = "/api/v1/projects"


async def test_create_project_makes_caller_owner(client: AsyncClient) -> None:
    payload = await register(client)
    project = await create_project(
        client,
        auth_headers(payload),
        name="  Apollo   Migration ",
        description="Move the monolith",
        status="planning",
        start_date="2026-01-01",
        due_date="2026-06-30",
    )

    assert project["name"] == "Apollo Migration"
    assert project["description"] == "Move the monolith"
    assert project["status"] == "planning"
    assert project["owner_id"] == payload["user"]["id"]
    assert project["owner_email"] == payload["user"]["email"]
    assert project["role"] == "owner"
    assert project["member_count"] == 1
    assert project["task_count"] == 0


async def test_create_project_validation(client: AsyncClient) -> None:
    payload = await register(client)
    headers = auth_headers(payload)

    blank = await client.post(PROJECTS, headers=headers, json={"name": "   "})
    assert blank.status_code == 422
    assert error_code(blank) == "validation_error"

    bad_dates = await client.post(
        PROJECTS,
        headers=headers,
        json={"name": "Backwards", "start_date": "2026-06-01", "due_date": "2026-01-01"},
    )
    assert bad_dates.status_code == 422
    assert error_code(bad_dates) == "validation_error"

    too_long = await client.post(PROJECTS, headers=headers, json={"name": "x" * 121})
    assert too_long.status_code == 422

    unauthenticated = await client.post(PROJECTS, json={"name": "No Auth"})
    assert unauthenticated.status_code == 401


async def test_list_projects_is_scoped_to_membership(client: AsyncClient) -> None:
    owner = await register(client, email="owner@example.com")
    outsider = await register(client, email="outsider@example.com")
    member = await register(client, email="member@example.com")

    project = await create_project(client, auth_headers(owner), name="Shared Roadmap")
    await create_project(client, auth_headers(owner), name="Private Roadmap")
    await add_member(client, auth_headers(owner), project["id"], member["user"]["email"])

    owner_list = await client.get(PROJECTS, headers=auth_headers(owner))
    assert owner_list.status_code == 200
    assert owner_list.json()["total"] == 2

    member_list = await client.get(PROJECTS, headers=auth_headers(member))
    body = member_list.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Shared Roadmap"
    assert body["items"][0]["role"] == "viewer"

    outsider_list = await client.get(PROJECTS, headers=auth_headers(outsider))
    assert outsider_list.json()["total"] == 0


async def test_list_projects_filters_and_sorts(client: AsyncClient) -> None:
    payload = await register(client)
    headers = auth_headers(payload)

    await create_project(client, headers, name="Beta", status="active")
    await create_project(client, headers, name="Alpha", status="planning")
    await create_project(client, headers, name="Gamma", status="planning")

    search = await client.get(PROJECTS, headers=headers, params={"search": "alp"})
    assert [item["name"] for item in search.json()["items"]] == ["Alpha"]

    filtered = await client.get(PROJECTS, headers=headers, params={"status": "planning"})
    names = [item["name"] for item in filtered.json()["items"]]
    assert set(names) == {"Alpha", "Gamma"}

    ascending = await client.get(PROJECTS, headers=headers, params={"sort": "name", "order": "asc"})
    assert [item["name"] for item in ascending.json()["items"]] == ["Alpha", "Beta", "Gamma"]

    descending = await client.get(
        PROJECTS, headers=headers, params={"sort": "name", "order": "desc"}
    )
    assert [item["name"] for item in descending.json()["items"]] == ["Gamma", "Beta", "Alpha"]


async def test_list_projects_paginates(client: AsyncClient) -> None:
    payload = await register(client)
    headers = auth_headers(payload)
    for index in range(5):
        await create_project(client, headers, name=f"Project {index}")

    first = await client.get(PROJECTS, headers=headers, params={"page": 1, "per_page": 2})
    body = first.json()
    assert body["total"] == 5
    assert body["pages"] == 3
    assert body["page"] == 1
    assert body["per_page"] == 2
    assert body["has_next"] is True
    assert body["has_prev"] is False
    assert len(body["items"]) == 2

    last = await client.get(PROJECTS, headers=headers, params={"page": 3, "per_page": 2})
    assert len(last.json()["items"]) == 1
    assert last.json()["has_next"] is False
    assert last.json()["has_prev"] is True

    empty = await client.get(PROJECTS, headers=headers, params={"page": 99, "per_page": 2})
    assert empty.json()["items"] == []
    assert empty.json()["pages"] == 3

    invalid = await client.get(PROJECTS, headers=headers, params={"per_page": 0})
    assert invalid.status_code == 422
    oversized = await client.get(PROJECTS, headers=headers, params={"per_page": 101})
    assert oversized.status_code == 422


async def test_get_update_and_delete_project(client: AsyncClient) -> None:
    payload = await register(client)
    headers = auth_headers(payload)
    project = await create_project(client, headers, name="Original")

    read = await client.get(f"{PROJECTS}/{project['id']}", headers=headers)
    assert read.status_code == 200
    assert read.json()["name"] == "Original"

    updated = await client.patch(
        f"{PROJECTS}/{project['id']}",
        headers=headers,
        json={"name": "  Renamed  Project ", "status": "on_hold", "due_date": "2026-12-31"},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["name"] == "Renamed Project"
    assert body["status"] == "on_hold"
    assert body["due_date"] == "2026-12-31"

    deleted = await client.delete(f"{PROJECTS}/{project['id']}", headers=headers)
    assert deleted.status_code == 204
    assert deleted.content == b""

    missing = await client.get(f"{PROJECTS}/{project['id']}", headers=headers)
    assert missing.status_code == 404
    assert error_code(missing) == "project_not_found"


async def test_update_rejects_inconsistent_dates(client: AsyncClient) -> None:
    payload = await register(client)
    headers = auth_headers(payload)
    project = await create_project(client, headers, start_date="2026-01-01")

    response = await client.patch(
        f"{PROJECTS}/{project['id']}", headers=headers, json={"due_date": "2025-01-01"}
    )
    assert response.status_code == 422
    assert error_code(response) == "validation_error"
    assert response.json()["error"]["details"][0]["field"] == "due_date"


async def test_non_members_cannot_see_other_tenants_projects(client: AsyncClient) -> None:
    owner = await register(client, email="owner2@example.com")
    outsider = await register(client, email="outsider2@example.com")
    project = await create_project(client, auth_headers(owner))

    for method, path, kwargs in (
        ("GET", f"{PROJECTS}/{project['id']}", {}),
        ("PATCH", f"{PROJECTS}/{project['id']}", {"json": {"name": "Hijacked"}}),
        ("DELETE", f"{PROJECTS}/{project['id']}", {}),
        ("GET", f"{PROJECTS}/{project['id']}/stats", {}),
        ("GET", f"{PROJECTS}/{project['id']}/members", {}),
        ("GET", f"{PROJECTS}/{project['id']}/tasks", {}),
        ("GET", f"{PROJECTS}/{project['id']}/report.pdf", {}),
    ):
        response = await client.request(method, path, headers=auth_headers(outsider), **kwargs)
        assert response.status_code == 404, path
        assert error_code(response) == "project_not_found", path


async def test_unknown_project_is_404(client: AsyncClient) -> None:
    payload = await register(client)
    response = await client.get(f"{PROJECTS}/{uuid.uuid4()}", headers=auth_headers(payload))
    assert response.status_code == 404
    assert error_code(response) == "project_not_found"


async def test_viewer_and_editor_permissions(client: AsyncClient) -> None:
    owner = await register(client, email="perm-owner@example.com")
    editor = await register(client, email="perm-editor@example.com")
    viewer = await register(client, email="perm-viewer@example.com")
    project = await create_project(client, auth_headers(owner))
    path = f"{PROJECTS}/{project['id']}"

    await add_member(client, auth_headers(owner), project["id"], editor["user"]["email"], "editor")
    await add_member(client, auth_headers(owner), project["id"], viewer["user"]["email"], "viewer")

    # Editors may modify content but not destroy the project.
    assert (
        await client.patch(path, headers=auth_headers(editor), json={"description": "Edited"})
    ).status_code == 200
    forbidden = await client.delete(path, headers=auth_headers(editor))
    assert forbidden.status_code == 403
    assert error_code(forbidden) == "insufficient_project_role"

    # Viewers are read-only.
    for method, subpath, kwargs in (
        ("PATCH", path, {"json": {"name": "Nope"}}),
        ("DELETE", path, {}),
        ("POST", f"{path}/members", {"json": {"email": editor["user"]["email"]}}),
        ("POST", f"{path}/tasks", {"json": {"title": "Nope"}}),
    ):
        response = await client.request(method, subpath, headers=auth_headers(viewer), **kwargs)
        assert response.status_code == 403, subpath
        assert error_code(response) == "insufficient_project_role", subpath

    assert (await client.get(path, headers=auth_headers(viewer))).status_code == 200


async def test_member_management_rules(client: AsyncClient, session_factory) -> None:
    owner = await register(client, email="mm-owner@example.com")
    invited = await register(client, email="mm-invited@example.com")
    project = await create_project(client, auth_headers(owner))
    owner_headers = auth_headers(owner)
    members_path = f"{PROJECTS}/{project['id']}/members"

    added = await add_member(
        client, owner_headers, project["id"], invited["user"]["email"], "editor"
    )
    assert added["role"] == "editor"
    assert added["email"] == invited["user"]["email"]

    listed = await client.get(members_path, headers=owner_headers)
    roles = [member["role"] for member in listed.json()]
    assert roles == ["owner", "editor"]

    # Duplicate invitations are rejected.
    duplicate = await client.post(
        members_path, headers=owner_headers, json={"email": invited["user"]["email"]}
    )
    assert duplicate.status_code == 409
    assert error_code(duplicate) == "already_member"

    # Unknown and deactivated users cannot be invited.
    unknown = await client.post(
        members_path, headers=owner_headers, json={"email": "ghost@example.com"}
    )
    assert unknown.status_code == 404
    assert error_code(unknown) == "user_not_found"

    inactive = await register(client, email="mm-inactive@example.com")
    await set_user_flag(session_factory, inactive["user"]["id"], is_active=False)
    disabled = await client.post(
        members_path, headers=owner_headers, json={"email": "mm-inactive@example.com"}
    )
    assert disabled.status_code == 400
    assert error_code(disabled) == "account_disabled"

    # Ownership cannot be granted through an invitation.
    owner_invite = await client.post(
        members_path,
        headers=owner_headers,
        json={
            "email": (await register(client, email="mm-third@example.com"))["user"]["email"],
            "role": "owner",
        },
    )
    assert owner_invite.status_code == 422

    # Role changes work; granting ownership and mutating the owner do not.
    promoted = await client.patch(
        f"{members_path}/{invited['user']['id']}",
        headers=owner_headers,
        json={"role": "viewer"},
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "viewer"

    grant_owner = await client.patch(
        f"{members_path}/{invited['user']['id']}",
        headers=owner_headers,
        json={"role": "owner"},
    )
    assert grant_owner.status_code == 422

    owner_role_change = await client.patch(
        f"{members_path}/{owner['user']['id']}",
        headers=owner_headers,
        json={"role": "editor"},
    )
    assert owner_role_change.status_code == 400
    assert error_code(owner_role_change) == "owner_role_immutable"

    missing_member = await client.patch(
        f"{members_path}/{uuid.uuid4()}", headers=owner_headers, json={"role": "viewer"}
    )
    assert missing_member.status_code == 404
    assert error_code(missing_member) == "member_not_found"


async def test_members_can_leave_but_owners_cannot(client: AsyncClient) -> None:
    owner = await register(client, email="leave-owner@example.com")
    member = await register(client, email="leave-member@example.com")
    project = await create_project(client, auth_headers(owner))
    await add_member(client, auth_headers(owner), project["id"], member["user"]["email"])
    members_path = f"{PROJECTS}/{project['id']}/members"

    owner_leaves = await client.delete(
        f"{members_path}/{owner['user']['id']}", headers=auth_headers(owner)
    )
    assert owner_leaves.status_code == 400
    assert error_code(owner_leaves) == "owner_cannot_leave"

    left = await client.delete(
        f"{members_path}/{member['user']['id']}", headers=auth_headers(member)
    )
    assert left.status_code == 200
    assert left.json()["message"] == "Member removed"

    # The project is no longer visible to the former member.
    assert (
        await client.get(f"{PROJECTS}/{project['id']}", headers=auth_headers(member))
    ).status_code == 404

    # Owners may remove others.
    await add_member(client, auth_headers(owner), project["id"], member["user"]["email"])
    removed = await client.delete(
        f"{members_path}/{member['user']['id']}", headers=auth_headers(owner)
    )
    assert removed.status_code == 200


async def test_project_stats(client: AsyncClient) -> None:
    payload = await register(client)
    headers = auth_headers(payload)
    project = await create_project(client, headers)

    await create_task(client, headers, project["id"], status="todo", priority="high")
    await create_task(client, headers, project["id"], status="in_progress", priority="low")
    await create_task(client, headers, project["id"], status="done", priority="high")
    await create_task(
        client,
        headers,
        project["id"],
        status="todo",
        priority="medium",
        due_date="2020-01-01",
    )

    response = await client.get(f"{PROJECTS}/{project['id']}/stats", headers=headers)
    assert response.status_code == 200
    stats = response.json()
    assert stats["project_id"] == project["id"]
    assert stats["total_tasks"] == 4
    assert stats["completed_tasks"] == 1
    assert stats["completion_rate"] == 25.0
    assert stats["overdue_tasks"] == 1
    assert stats["by_status"]["todo"] == 2
    assert stats["by_status"]["in_progress"] == 1
    assert stats["by_status"]["done"] == 1
    assert stats["by_status"]["blocked"] == 0
    assert stats["by_priority"]["high"] == 2

    detail = await client.get(f"{PROJECTS}/{project['id']}", headers=headers)
    assert detail.json()["task_count"] == 4


async def test_deleting_project_cascades_to_tasks(client: AsyncClient) -> None:
    payload = await register(client)
    headers = auth_headers(payload)
    project = await create_project(client, headers)
    task = await create_task(client, headers, project["id"])

    assert (await client.delete(f"{PROJECTS}/{project['id']}", headers=headers)).status_code == 204

    response = await client.get(f"{PROJECTS}/{project['id']}/tasks/{task['id']}", headers=headers)
    assert response.status_code == 404


async def test_admins_can_access_every_project(client: AsyncClient, session_factory) -> None:
    owner = await register(client, email="admin-target@example.com")
    admin = await register(client, email="admin-actor@example.com")
    await promote_to_admin(session_factory, admin["user"]["id"])
    project = await create_project(client, auth_headers(owner), name="Admin Visible")

    listed = await client.get(PROJECTS, headers=auth_headers(admin))
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["role"] == "owner"

    read = await client.get(f"{PROJECTS}/{project['id']}", headers=auth_headers(admin))
    assert read.status_code == 200

    updated = await client.patch(
        f"{PROJECTS}/{project['id']}", headers=auth_headers(admin), json={"status": "completed"}
    )
    assert updated.status_code == 200
