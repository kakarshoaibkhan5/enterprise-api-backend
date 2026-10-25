"""PDF project reports: HTTP contract, filtering, and the renderer."""

from __future__ import annotations

from datetime import UTC, date, datetime

from httpx import AsyncClient

from app.schemas.project import ProjectStats
from app.services import pdf as pdf_service
from tests.helpers import auth_headers, create_project, create_task, error_code, register

PROJECTS = "/api/v1/projects"


def _stats(project_id: str, *, total: int, done: int) -> ProjectStats:
    return ProjectStats(
        project_id=project_id,
        total_tasks=total,
        by_status={"todo": total - done, "in_progress": 0, "blocked": 0, "done": done},
        by_priority={"low": 0, "medium": total, "high": 0, "critical": 0},
        completed_tasks=done,
        overdue_tasks=0,
        completion_rate=round(done / total * 100, 2) if total else 0.0,
    )


def test_slugify_filename_is_url_and_header_safe() -> None:
    assert pdf_service.slugify_filename("Apollo Migration") == "apollo-migration"
    assert pdf_service.slugify_filename("  Q1 / Q2: Roadmap!! ") == "q1-q2-roadmap"
    assert pdf_service.slugify_filename("***") == "project"
    assert pdf_service.slugify_filename("A" * 120) == "a" * 60


def test_render_report_produces_a_pdf() -> None:
    now = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
    data = pdf_service.build_report_data(
        project_id="11111111-1111-1111-1111-111111111111",
        project_name="Atlas & <Sons>",
        description="Line one\nLine two with <angle brackets>",
        status="active",
        owner_name="Ada Lovelace",
        owner_email="ada@example.com",
        start_date=date(2026, 1, 1),
        due_date=date(2026, 6, 30),
        created_at=now,
        stats=_stats("11111111-1111-1111-1111-111111111111", total=3, done=1),
        tasks=[
            pdf_service.ReportTask(
                title="Ship <the> release",
                status="in_progress",
                priority="high",
                assignee="Grace Hopper",
                due_date=date(2026, 3, 1),
                labels=["backend", "release"],
            ),
            pdf_service.ReportTask(
                title="Close the milestone",
                status="done",
                priority="medium",
                assignee=None,
                due_date=None,
            ),
        ],
        members=[
            pdf_service.ReportMember(name="Ada", email="ada@example.com", role="owner"),
            pdf_service.ReportMember(name="Grace", email="grace@example.com", role="editor"),
        ],
        generated_by="Ada Lovelace",
        generated_at=now,
        truncated=True,
    )

    content = pdf_service.render_report(data)
    assert content.startswith(b"%PDF-")
    assert content.rstrip().endswith(b"%%EOF")
    assert len(content) > 1_000


def test_render_report_handles_empty_project() -> None:
    now = datetime(2026, 2, 1, tzinfo=UTC)
    placeholder = "22222222-2222-2222-2222-222222222222"
    data = pdf_service.build_report_data(
        project_id=placeholder,
        project_name="Empty",
        description=None,
        status="planning",
        owner_name=None,
        owner_email="owner@example.com",
        start_date=None,
        due_date=None,
        created_at=now,
        stats=_stats(placeholder, total=0, done=0),
        tasks=[],
        members=[],
        generated_by="owner@example.com",
        generated_at=now,
    )

    content = pdf_service.render_report(data)
    assert content.startswith(b"%PDF-")


async def test_download_report_http_contract(client: AsyncClient) -> None:
    payload = await register(client)
    headers = auth_headers(payload)
    project = await create_project(client, headers, name="Apollo Migration")
    await create_task(client, headers, project["id"], title="Cut over", status="done")

    response = await client.get(f"{PROJECTS}/{project['id']}/report.pdf", headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment; filename=")
    assert disposition.endswith(f'apollo-migration-report-{date.today():%Y%m%d}.pdf"')
    assert int(response.headers["content-length"]) == len(response.content)
    assert response.headers["cache-control"] == "no-store"
    assert response.content.startswith(b"%PDF-")


async def test_report_filtering_is_applied_before_rendering(
    client: AsyncClient, monkeypatch
) -> None:
    payload = await register(client)
    headers = auth_headers(payload)
    project = await create_project(client, headers)
    await create_task(client, headers, project["id"], title="Still open", status="todo")
    await create_task(client, headers, project["id"], title="Already done", status="done")

    captured: list[pdf_service.ReportData] = []

    def fake_render(data: pdf_service.ReportData) -> bytes:
        captured.append(data)
        return b"%PDF-fake"

    monkeypatch.setattr(pdf_service, "render_report", fake_render)

    default = await client.get(f"{PROJECTS}/{project['id']}/report.pdf", headers=headers)
    assert default.status_code == 200
    assert len(captured[-1].tasks) == 2

    filtered = await client.get(
        f"{PROJECTS}/{project['id']}/report.pdf",
        headers=headers,
        params={"include_completed": "false"},
    )
    assert filtered.status_code == 200
    assert [task.title for task in captured[-1].tasks] == ["Still open"]
    assert captured[-1].stats.total_tasks == 2
    assert len(captured[-1].members) == 1
    assert captured[-1].members[0].role == "owner"
    assert captured[-1].generated_by == "Test User"

    empty = await client.get(
        f"{PROJECTS}/{project['id']}/report.pdf",
        headers=headers,
        params={"include_completed": "false"},
    )
    assert empty.status_code == 200


async def test_report_requires_membership(client: AsyncClient) -> None:
    owner = await register(client, email="report-owner@example.com")
    outsider = await register(client, email="report-outsider@example.com")
    project = await create_project(client, auth_headers(owner))

    response = await client.get(
        f"{PROJECTS}/{project['id']}/report.pdf", headers=auth_headers(outsider)
    )
    assert response.status_code == 404
    assert error_code(response) == "project_not_found"

    anonymous = await client.get(f"{PROJECTS}/{project['id']}/report.pdf")
    assert anonymous.status_code == 401


async def test_report_truncates_at_configured_limit(client: AsyncClient, monkeypatch) -> None:
    from app.api.v1 import projects as projects_module

    payload = await register(client)
    headers = auth_headers(payload)
    project = await create_project(client, headers)
    for index in range(3):
        await create_task(client, headers, project["id"], title=f"Task {index}")

    monkeypatch.setattr(projects_module.settings, "pdf_max_tasks", 2)
    captured: list[pdf_service.ReportData] = []
    monkeypatch.setattr(
        pdf_service, "render_report", lambda data: captured.append(data) or b"%PDF-fake"
    )

    response = await client.get(f"{PROJECTS}/{project['id']}/report.pdf", headers=headers)
    assert response.status_code == 200
    assert len(captured[-1].tasks) == 2
    assert captured[-1].truncated is True
