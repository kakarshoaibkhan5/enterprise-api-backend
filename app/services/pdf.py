"""PDF report rendering with ReportLab.

Rendering is pure CPU work on primitive data: the API layer converts ORM
objects into :class:`ReportData` first, then runs :func:`render_report` in a
threadpool so the event loop is never blocked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.schemas.project import ProjectStats

BRAND = colors.HexColor("#1d4ed8")
INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#6b7280")
BORDER = colors.HexColor("#e5e7eb")
SURFACE = colors.HexColor("#f3f4f6")

STATUS_COLORS = {
    "todo": colors.HexColor("#6b7280"),
    "in_progress": colors.HexColor("#2563eb"),
    "blocked": colors.HexColor("#dc2626"),
    "done": colors.HexColor("#059669"),
}

PRIORITY_COLORS = {
    "low": colors.HexColor("#6b7280"),
    "medium": colors.HexColor("#2563eb"),
    "high": colors.HexColor("#d97706"),
    "critical": colors.HexColor("#dc2626"),
}

STATUS_LABELS = {
    "todo": "To do",
    "in_progress": "In progress",
    "blocked": "Blocked",
    "done": "Done",
}


@dataclass(slots=True)
class ReportTask:
    title: str
    status: str
    priority: str
    assignee: str | None
    due_date: date | None
    labels: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ReportMember:
    name: str
    email: str
    role: str


@dataclass(slots=True)
class ReportData:
    project_name: str
    project_id: str
    description: str | None
    status: str
    owner_name: str | None
    owner_email: str
    start_date: date | None
    due_date: date | None
    created_at: datetime
    stats: ProjectStats
    tasks: list[ReportTask]
    members: list[ReportMember]
    generated_by: str
    generated_at: datetime
    truncated: bool = False


def build_report_data(
    *,
    project_id: str,
    project_name: str,
    description: str | None,
    status: str,
    owner_name: str | None,
    owner_email: str,
    start_date: date | None,
    due_date: date | None,
    created_at: datetime,
    stats: ProjectStats,
    tasks: list[ReportTask],
    members: list[ReportMember],
    generated_by: str,
    generated_at: datetime,
    truncated: bool = False,
) -> ReportData:
    return ReportData(
        project_id=project_id,
        project_name=project_name,
        description=description,
        status=status,
        owner_name=owner_name,
        owner_email=owner_email,
        start_date=start_date,
        due_date=due_date,
        created_at=created_at,
        stats=stats,
        tasks=tasks,
        members=members,
        generated_by=generated_by,
        generated_at=generated_at,
        truncated=truncated,
    )


def slugify_filename(name: str) -> str:
    """Build a safe, download-friendly filename fragment."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (slug or "project")[:60]


class _NumberedCanvas(pdf_canvas.Canvas):
    """Two-pass canvas so footers can show 'Page X of Y'."""

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self._saved_pages: list[dict] = []

    def showPage(self) -> None:  # noqa: N802 - ReportLab API
        self._saved_pages.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._saved_pages)
        for state in self._saved_pages:
            self.__dict__.update(state)
            self._draw_footer(total)
            super().showPage()
        super().save()

    def _draw_footer(self, total: int) -> None:
        width, _height = A4
        self.saveState()
        self.setStrokeColor(BORDER)
        self.setLineWidth(0.5)
        self.line(20 * mm, 15 * mm, width - 20 * mm, 15 * mm)
        self.setFont("Helvetica", 8)
        self.setFillColor(MUTED)
        self.drawString(20 * mm, 10 * mm, "Enterprise API - Confidential")
        self.drawRightString(width - 20 * mm, 10 * mm, f"Page {self._pageNumber} of {total}")
        self.restoreState()


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=24,
            textColor=INK,
            alignment=TA_LEFT,
            spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "ReportSubtitle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            textColor=MUTED,
        ),
        "h2": ParagraphStyle(
            "ReportH2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=16,
            textColor=INK,
            spaceBefore=10,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "ReportBody",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            textColor=INK,
        ),
        "cell": ParagraphStyle(
            "ReportCell",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            textColor=INK,
        ),
        "cell_bold": ParagraphStyle(
            "ReportCellBold",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=INK,
        ),
        "label": ParagraphStyle(
            "ReportLabel",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=MUTED,
        ),
    }


def _p(text: str | None, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text or "-"), style)


def _format_date(value: date | None) -> str:
    return value.isoformat() if value else "-"


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M UTC")


def _summary_table(data: ReportData, styles: dict[str, ParagraphStyle]) -> Table:
    stats = data.stats
    metrics = [
        ("Total tasks", str(stats.total_tasks)),
        ("Completed", str(stats.completed_tasks)),
        ("In progress", str(stats.by_status.get("in_progress", 0))),
        ("Blocked", str(stats.by_status.get("blocked", 0))),
        ("Overdue", str(stats.overdue_tasks)),
        ("Completion", f"{stats.completion_rate:.1f}%"),
    ]
    cells = [
        [Paragraph(f"<b>{value}</b>", styles["title"]), Paragraph(label, styles["label"])]
        for label, value in metrics
    ]
    # Lay the metrics out as a 3x2 grid.
    rows = []
    for index in range(0, len(cells), 3):
        row: list = []
        for value_para, label_para in cells[index : index + 3]:
            row.extend([value_para, label_para])
        rows.append(row)
    table = Table(rows, colWidths=[18 * mm, 44 * mm] * 3, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SURFACE),
                ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.white),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def _details_table(data: ReportData, styles: dict[str, ParagraphStyle]) -> Table:
    rows = [
        [
            _p("Status", styles["label"]),
            _p(
                STATUS_LABELS.get(data.status, data.status.replace("_", " ").title()),
                styles["cell"],
            ),
            _p("Owner", styles["label"]),
            _p(
                f"{data.owner_name} <{data.owner_email}>" if data.owner_name else data.owner_email,
                styles["cell"],
            ),
        ],
        [
            _p("Start date", styles["label"]),
            _p(_format_date(data.start_date), styles["cell"]),
            _p("Due date", styles["label"]),
            _p(_format_date(data.due_date), styles["cell"]),
        ],
        [
            _p("Created", styles["label"]),
            _p(_format_datetime(data.created_at), styles["cell"]),
            _p("Members", styles["label"]),
            _p(str(len(data.members)), styles["cell"]),
        ],
        [
            _p("Project ID", styles["label"]),
            _p(data.project_id, styles["cell"]),
            _p("Generated", styles["label"]),
            _p(f"{_format_datetime(data.generated_at)} by {data.generated_by}", styles["cell"]),
        ],
    ]
    table = Table(rows, colWidths=[24 * mm, 60 * mm, 24 * mm, 65 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, BORDER),
            ]
        )
    )
    return table


def _tasks_table(data: ReportData, styles: dict[str, ParagraphStyle]) -> Table:
    header = [
        Paragraph("Task", styles["cell_bold"]),
        Paragraph("Status", styles["cell_bold"]),
        Paragraph("Priority", styles["cell_bold"]),
        Paragraph("Assignee", styles["cell_bold"]),
        Paragraph("Due", styles["cell_bold"]),
    ]
    rows: list[list] = [header]
    for task in data.tasks:
        title = task.title
        if task.labels:
            title += f"<br/><font size=7 color='#6b7280'>{escape(', '.join(task.labels))}</font>"
        rows.append(
            [
                Paragraph(escape(title), styles["cell"]),
                Paragraph(
                    f"<font color='{STATUS_COLORS.get(task.status, '#111827').hexval()}'>"
                    f"{escape(STATUS_LABELS.get(task.status, task.status))}</font>",
                    styles["cell"],
                ),
                Paragraph(
                    f"<font color='{PRIORITY_COLORS.get(task.priority, '#111827').hexval()}'>"
                    f"{escape(task.priority.title())}</font>",
                    styles["cell"],
                ),
                Paragraph(escape(task.assignee or "Unassigned"), styles["cell"]),
                Paragraph(_format_date(task.due_date), styles["cell"]),
            ]
        )

    table = Table(
        rows,
        colWidths=[82 * mm, 24 * mm, 20 * mm, 32 * mm, 22 * mm],
        repeatRows=1,
        hAlign="LEFT",
    )
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), BRAND),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    for index in range(1, len(rows)):
        if index % 2 == 0:
            style.append(("BACKGROUND", (0, index), (-1, index), SURFACE))
    table.setStyle(TableStyle(style))
    return table


def _members_table(data: ReportData, styles: dict[str, ParagraphStyle]) -> Table:
    rows: list[list] = [
        [
            Paragraph("Name", styles["cell_bold"]),
            Paragraph("Email", styles["cell_bold"]),
            Paragraph("Role", styles["cell_bold"]),
        ]
    ]
    for member in data.members:
        rows.append(
            [
                Paragraph(escape(member.name), styles["cell"]),
                Paragraph(escape(member.email), styles["cell"]),
                Paragraph(escape(member.role.title()), styles["cell"]),
            ]
        )
    table = Table(rows, colWidths=[60 * mm, 80 * mm, 30 * mm], repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), SURFACE),
                ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def render_report(data: ReportData) -> bytes:
    """Render ``data`` to PDF bytes. Safe to call from a worker thread."""
    buffer = BytesIO()
    styles = _styles()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=22 * mm,
        title=f"Project report - {data.project_name}",
        author="Enterprise API",
        subject="Project status report",
        creator="enterprise-api-backend",
    )

    story: list = [
        Paragraph("Project Status Report", styles["subtitle"]),
        Paragraph(escape(data.project_name), styles["title"]),
        Spacer(1, 4),
        Paragraph(
            f"Project ID {escape(data.project_id)} &nbsp;|&nbsp; "
            f"Generated {_format_datetime(data.generated_at)}",
            styles["subtitle"],
        ),
        Spacer(1, 12),
        _summary_table(data, styles),
        Spacer(1, 10),
        Paragraph("Overview", styles["h2"]),
        _details_table(data, styles),
    ]

    if data.description:
        story.extend(
            [
                Paragraph("Description", styles["h2"]),
                Paragraph(escape(data.description).replace("\n", "<br/>"), styles["body"]),
            ]
        )

    story.extend([Paragraph("Tasks", styles["h2"])])
    if data.tasks:
        story.append(_tasks_table(data, styles))
        if data.truncated:
            story.append(
                Paragraph(
                    "<i>Task list truncated to the configured report limit.</i>",
                    styles["subtitle"],
                )
            )
    else:
        story.append(Paragraph("No tasks match the selected filters.", styles["body"]))

    if data.members:
        story.append(PageBreak())
        story.append(Paragraph("Members", styles["h2"]))
        story.append(_members_table(data, styles))

    story.append(Spacer(1, 10))
    story.append(
        KeepTogether(
            Paragraph(
                "This report was generated automatically from live project data. "
                "Figures reflect the state of the project at the generation time.",
                styles["subtitle"],
            )
        )
    )

    document.build(story, canvasmaker=_NumberedCanvas)
    return buffer.getvalue()
