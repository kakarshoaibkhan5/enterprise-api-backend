"""Tasks: lifecycle, filtering, sorting, assignment, and bulk updates."""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from tests.helpers import (
    add_member,
    auth_headers,
    create_project,
    create_task,
    error_code,
    register,
)

TASKS = "/api/v1/projects/{project_id}/tasks"


def tasks_path(project_id: str) -> str:
    return TASKS.format(project_id=project_id)


async def test_create_task_normalises_input(client: AsyncClient) -> None:
    payload = await register(client)
    headers = auth_headers(payload)
    project = await create_project(client, headers)

    task = await create_task(
        client,
        headers,
        project["id"],
        title="  Write   the   runbook ",
        description="  Cover rollback steps.  ",
        priority="high",
        labels=["  Backend ", "backend", "Urgent"],
        due_date="2026-03-01",
    )

    assert task["title"] == "Write the runbook"
    assert task["description"] == "Cover rollback steps."
    assert task["status"] == "todo"
    assert task["priority"] == "high"
    assert task["labels"] == ["Backend", "Urgent"]
    assert task["due_date"] == "2026-03-01"
    assert task["completed_at"] is None
    assert task["created_by"]["id"] == payload["user"]["id"]
    assert task["assignee"] is None
    assert task["project_id"] == project["id"]


async def test_create_task_validation(client: AsyncClient) -> None:
    payload = await register(client)
    headers = auth_headers(payload)
    project = await create_project(client, headers)
    path = tasks_path(project["id"])

    blank = await client.post(path, headers=headers, json={"title": "   "})
    assert blank.status_code == 422
    assert error_code(blank) == "validation_error"

    too_long = await client.post(path, headers=headers, json={"title": "x" * 201})
    assert too_long.status_code == 422

    bad_label = await client.post(path, headers=headers, json={"title": "T", "labels": ["  "]})
    assert bad_label.status_code == 422

    too_many_labels = await client.post(
        path, headers=headers, json={"title": "T", "labels": [f"l{i}" for i in range(21)]}
    )
    assert too_many_labels.status_code == 422

    long_label = await client.post(path, headers=headers, json={"title": "T", "labels": ["x" * 41]})
    assert long_label.status_code == 422

    bad_status = await client.post(path, headers=headers, json={"title": "T", "status": "archived"})
    assert bad_status.status_code == 422


async def test_assignee_must_be_project_member(client: AsyncClient) -> None:
    owner = await register(client, email="assign-owner@example.com")
    member = await register(client, email="assign-member@example.com")
    outsider = await register(client, email="assign-outsider@example.com")
    owner_headers = auth_headers(owner)
    project = await create_project(client, owner_headers)
    path = tasks_path(project["id"])

    await add_member(client, owner_headers, project["id"], member["user"]["email"], "editor")

    # The owner does not need an explicit membership row to be assigned.
    assigned_owner = await create_task(
        client, owner_headers, project["id"], assignee_id=owner["user"]["id"]
    )
    assert assigned_owner["assignee"]["email"] == owner["user"]["email"]

    assigned_member = await create_task(
        client, owner_headers, project["id"], assignee_id=member["user"]["id"]
    )
    assert assigned_member["assignee"]["email"] == member["user"]["email"]

    rejected = await client.post(
        path, headers=owner_headers, json={"title": "Nope", "assignee_id": outsider["user"]["id"]}
    )
    assert rejected.status_code == 422
    assert error_code(rejected) == "validation_error"
    detail = rejected.json()["error"]["details"][0]
    assert detail["field"] == "assignee_id"
    assert "member" in detail["message"]

    update_rejected = await client.patch(
        f"{path}/{assigned_member['id']}",
        headers=owner_headers,
        json={"assignee_id": outsider["user"]["id"]},
    )
    assert update_rejected.status_code == 422


async def test_update_task_lifecycle_and_completion_timestamp(client: AsyncClient) -> None:
    payload = await register(client)
    headers = auth_headers(payload)
    project = await create_project(client, headers)
    task = await create_task(client, headers, project["id"])
    path = f"{tasks_path(project['id'])}/{task['id']}"

    done = await client.patch(path, headers=headers, json={"status": "done"})
    assert done.status_code == 200
    assert done.json()["status"] == "done"
    assert done.json()["completed_at"] is not None

    reopened = await client.patch(path, headers=headers, json={"status": "in_progress"})
    assert reopened.status_code == 200
    assert reopened.json()["completed_at"] is None

    updated = await client.patch(
        path,
        headers=headers,
        json={"title": "Rewritten", "priority": "critical", "labels": ["one"]},
    )
    assert updated.json()["title"] == "Rewritten"
    assert updated.json()["priority"] == "critical"
    assert updated.json()["labels"] == ["one"]

    cleared = await client.patch(path, headers=headers, json={"labels": []})
    assert cleared.json()["labels"] == []


async def test_task_created_in_done_state_has_completion_time(client: AsyncClient) -> None:
    payload = await register(client)
    headers = auth_headers(payload)
    project = await create_project(client, headers)
    task = await create_task(client, headers, project["id"], status="done")
    assert task["completed_at"] is not None


async def test_list_tasks_filters_and_sorts(client: AsyncClient) -> None:
    payload = await register(client)
    headers = auth_headers(payload)
    project = await create_project(client, headers)
    path = tasks_path(project["id"])

    await create_task(
        client,
        headers,
        project["id"],
        title="Alpha deploy",
        description="kubernetes rollout",
        status="todo",
        priority="low",
    )
    await create_task(
        client,
        headers,
        project["id"],
        title="Bravo test",
        description="integration suite",
        status="in_progress",
        priority="high",
    )
    await create_task(
        client,
        headers,
        project["id"],
        title="Charlie review",
        status="blocked",
        priority="critical",
    )
    await create_task(client, headers, project["id"], title="Delta done", status="done")

    def titles(body: dict) -> list[str]:
        return [item["title"] for item in body["items"]]

    by_status = await client.get(path, headers=headers, params={"status": "blocked"})
    assert titles(by_status.json()) == ["Charlie review"]

    by_priority = await client.get(path, headers=headers, params={"priority": "high"})
    assert titles(by_priority.json()) == ["Bravo test"]

    by_search = await client.get(path, headers=headers, params={"search": "KUBERNETES"})
    assert titles(by_search.json()) == ["Alpha deploy"]

    by_title_search = await client.get(path, headers=headers, params={"search": "review"})
    assert titles(by_title_search.json()) == ["Charlie review"]

    title_asc = await client.get(path, headers=headers, params={"sort": "title", "order": "asc"})
    assert titles(title_asc.json()) == [
        "Alpha deploy",
        "Bravo test",
        "Charlie review",
        "Delta done",
    ]

    priority_desc = await client.get(
        path, headers=headers, params={"sort": "priority", "order": "desc"}
    )
    assert titles(priority_desc.json())[0] == "Charlie review"

    status_desc = await client.get(
        path, headers=headers, params={"sort": "status", "order": "desc"}
    )
    assert titles(status_desc.json()) == [
        "Charlie review",
        "Bravo test",
        "Alpha deploy",
        "Delta done",
    ]

    invalid_sort = await client.get(path, headers=headers, params={"sort": "nope"})
    assert invalid_sort.status_code == 422


async def test_list_tasks_filters_by_assignee_and_paginates(client: AsyncClient) -> None:
    owner = await register(client, email="page-owner@example.com")
    member = await register(client, email="page-member@example.com")
    headers = auth_headers(owner)
    project = await create_project(client, headers)
    path = tasks_path(project["id"])
    await add_member(client, headers, project["id"], member["user"]["email"], "editor")

    mine = await create_task(
        client, headers, project["id"], title="Mine", assignee_id=owner["user"]["id"]
    )
    await create_task(
        client, headers, project["id"], title="Theirs", assignee_id=member["user"]["id"]
    )

    filtered = await client.get(path, headers=headers, params={"assignee_id": owner["user"]["id"]})
    assert [item["id"] for item in filtered.json()["items"]] == [mine["id"]]

    for index in range(4):
        await create_task(client, headers, project["id"], title=f"Bulk {index}")

    page = await client.get(path, headers=headers, params={"page": 1, "per_page": 2})
    body = page.json()
    assert body["total"] == 6
    assert body["pages"] == 3
    assert body["has_next"] is True
    assert len(body["items"]) == 2


async def test_task_isolation_between_projects(client: AsyncClient) -> None:
    payload = await register(client)
    headers = auth_headers(payload)
    first = await create_project(client, headers, name="First")
    second = await create_project(client, headers, name="Second")
    task = await create_task(client, headers, first["id"])

    wrong_project = await client.get(f"{tasks_path(second['id'])}/{task['id']}", headers=headers)
    assert wrong_project.status_code == 404
    assert error_code(wrong_project) == "task_not_found"

    unknown = await client.get(f"{tasks_path(first['id'])}/{uuid.uuid4()}", headers=headers)
    assert unknown.status_code == 404
    assert error_code(unknown) == "task_not_found"

    deleted = await client.delete(f"{tasks_path(first['id'])}/{task['id']}", headers=headers)
    assert deleted.status_code == 204
    gone = await client.get(f"{tasks_path(first['id'])}/{task['id']}", headers=headers)
    assert gone.status_code == 404


async def test_bulk_status_update(client: AsyncClient) -> None:
    payload = await register(client)
    headers = auth_headers(payload)
    project = await create_project(client, headers)
    path = tasks_path(project["id"])
    first = await create_task(client, headers, project["id"])
    second = await create_task(client, headers, project["id"])
    third = await create_task(client, headers, project["id"])

    response = await client.post(
        f"{path}/bulk-status",
        headers=headers,
        json={"task_ids": [first["id"], second["id"]], "status": "done"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["updated"] == 2
    assert set(body["task_ids"]) == {first["id"], second["id"]}

    for task_id in (first["id"], second["id"]):
        task = await client.get(f"{path}/{task_id}", headers=headers)
        assert task.json()["status"] == "done"
        assert task.json()["completed_at"] is not None

    untouched = await client.get(f"{path}/{third['id']}", headers=headers)
    assert untouched.json()["status"] == "todo"

    # Ids from another project simply do not match.
    other = await create_project(client, headers, name="Other")
    foreign = await create_task(client, headers, other["id"])
    missing = await client.post(
        f"{path}/bulk-status", headers=headers, json={"task_ids": [foreign["id"]], "status": "done"}
    )
    assert missing.status_code == 404
    assert error_code(missing) == "task_not_found"

    empty = await client.post(
        f"{path}/bulk-status", headers=headers, json={"task_ids": [], "status": "done"}
    )
    assert empty.status_code == 422

    oversized = await client.post(
        f"{path}/bulk-status",
        headers=headers,
        json={"task_ids": [str(uuid.uuid4()) for _ in range(101)], "status": "done"},
    )
    assert oversized.status_code == 422


async def test_viewer_cannot_mutate_tasks(client: AsyncClient) -> None:
    owner = await register(client, email="view-owner@example.com")
    viewer = await register(client, email="view-viewer@example.com")
    headers = auth_headers(owner)
    project = await create_project(client, headers)
    await add_member(client, headers, project["id"], viewer["user"]["email"], "viewer")
    task = await create_task(client, headers, project["id"])
    viewer_headers = auth_headers(viewer)
    path = tasks_path(project["id"])

    assert (await client.get(path, headers=viewer_headers)).status_code == 200
    assert (await client.get(f"{path}/{task['id']}", headers=viewer_headers)).status_code == 200

    attempts = (
        ("POST", path, {"json": {"title": "Nope"}}),
        ("PATCH", f"{path}/{task['id']}", {"json": {"title": "Nope"}}),
        ("DELETE", f"{path}/{task['id']}", {}),
        ("POST", f"{path}/bulk-status", {"json": {"task_ids": [task["id"]], "status": "done"}}),
    )
    for method, subpath, kwargs in attempts:
        response = await client.request(method, subpath, headers=viewer_headers, **kwargs)
        assert response.status_code == 403
        assert error_code(response) == "insufficient_project_role"


async def test_editor_can_manage_tasks(client: AsyncClient) -> None:
    owner = await register(client, email="edit-owner@example.com")
    editor = await register(client, email="edit-editor@example.com")
    project = await create_project(client, auth_headers(owner))
    await add_member(client, auth_headers(owner), project["id"], editor["user"]["email"], "editor")
    editor_headers = auth_headers(editor)

    task = await create_task(client, editor_headers, project["id"], title="By editor")
    path = f"{tasks_path(project['id'])}/{task['id']}"
    assert (
        await client.patch(path, headers=editor_headers, json={"status": "done"})
    ).status_code == 200
    assert (await client.delete(path, headers=editor_headers)).status_code == 204
