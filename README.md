# Enterprise API Backend

A production-grade REST API for project and task management, built with **FastAPI**,
**async SQLAlchemy 2.x**, **PostgreSQL**, and **JWT authentication**. It ships with
multi-tenant projects, role-based membership, task workflows, server-rendered PDF
reports, consistent error handling, request tracing, rate limiting, migrations, tests,
Docker packaging, and CI.

> **Status:** complete reference implementation. Tests: **123 passing**. Lint: **ruff clean**.

---

## Table of contents

- [Features](#features)
- [Tech stack](#tech-stack)
- [Quick start](#quick-start)
- [Docker](#docker)
- [Configuration](#configuration)
- [Authentication flow](#authentication-flow)
- [API overview](#api-overview)
- [Examples](#examples)
- [PDF reports](#pdf-reports)
- [Project layout](#project-layout)
- [Database & migrations](#database--migrations)
- [Testing](#testing)
- [Security notes](#security-notes)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)

---

## Features

**Authentication & sessions**

- Email/password registration and login with bcrypt password hashing.
- Short-lived JWT access tokens and long-lived, **single-use rotating** refresh tokens.
- Refresh-token reuse detection: replaying a revoked token terminates every session.
- Immediate revocation through a `ver` (token version) claim — `logout-all`,
  password changes, role changes, deactivation, and account deletion all invalidate
  outstanding access tokens.
- OAuth2 password flow wired into Swagger UI's **Authorize** dialog.
- Per-account admin controls: promote/demote, activate/deactivate, with last-admin
  and self-lockout safeguards.

**Projects & multi-tenancy**

- Owner / editor / viewer roles per project.
- Non-members receive `404` rather than `403`, so project existence is never leaked.
- Member invitations by email, role changes, self-service leave, and owner-only
  destructive operations.
- Aggregate counters (members, tasks) and per-request effective role on every project.

**Tasks**

- Status (`todo`, `in_progress`, `blocked`, `done`), priority
  (`low`, `medium`, `high`, `critical`), labels, assignees, and due dates.
- Automatic `completed_at` maintenance when a task enters or leaves `done`.
- Filtering by status, priority, assignee, and free-text search.
- Sorting and page-based pagination on every collection endpoint.
- Bulk status updates for up to 100 tasks in one transaction.
- Assignees must be project members (validated server-side).

**Reporting**

- `GET /projects/{id}/report.pdf` streams a branded, printable PDF with project
  metadata, task statistics, the task list, and the team roster.
- Rendering runs in a thread pool so the event loop is never blocked.
- Oversized reports are truncated at `PDF_MAX_TASKS` and flagged in the document.

**Platform**

- Consistent error envelope on every failure with a correlated `request_id`.
- Request IDs accepted from `X-Request-ID` or generated, echoed in responses and logs.
- Security headers (CSP, `X-Content-Type-Options`, `Referrer-Policy`, ...) and HSTS
  in production.
- CORS, trusted-host validation, request body size limits, and in-process sliding-window
  rate limiting on auth and report endpoints.
- Health endpoints: `/health/live` (process) and `/health/ready` (database).
- OpenAPI 3.1 schema, Swagger UI, ReDoc, and a committed `docs/openapi.json`.
- Alembic migrations, idempotent demo seeding, multi-stage Docker image, Compose stack,
  and GitHub Actions CI.

## Tech stack

| Layer | Choice |
| --- | --- |
| Runtime | Python 3.11+ |
| Web framework | FastAPI |
| ASGI server | Uvicorn |
| ORM | SQLAlchemy 2.x (async) |
| Database | PostgreSQL 17 (SQLite for local/tests) |
| Migrations | Alembic |
| Validation | Pydantic v2 / pydantic-settings |
| Auth | PyJWT (HS256/384/512), bcrypt |
| PDF | ReportLab |
| Tests | pytest, pytest-asyncio, httpx |
| Lint/format | ruff |
| Packaging | setuptools, Docker |

## Quick start

### Prerequisites

- Python 3.11 or newer
- PostgreSQL 14+ for a production-like run (SQLite works out of the box for a first look)

### 1. Install

```bash
git clone https://github.com/your-org/enterprise-api-backend.git
cd enterprise-api-backend

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,postgres]"
```

Or use the Makefile, which creates `.venv` for you:

```bash
make install
```

### 2. Configure

```bash
cp .env.example .env
```

Generate a real secret before running anywhere shared:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Put it in `.env` as `SECRET_KEY=...`.

The default `.env.example` uses SQLite and `AUTO_CREATE_TABLES=true`, which is enough
to boot locally. For PostgreSQL, set:

```dotenv
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/enterprise_api
AUTO_CREATE_TABLES=false
```

and create the schema with Alembic:

```bash
alembic upgrade head
```

### 3. Run

```bash
uvicorn app.main:app --reload --port 8000
# or
make run
```

Open:

- Swagger UI — <http://127.0.0.1:8000/docs>
- ReDoc — <http://127.0.0.1:8000/redoc>
- OpenAPI JSON — <http://127.0.0.1:8000/openapi.json>
- Readiness — <http://127.0.0.1:8000/health/ready>

### 4. Create data

Register through the API or load the demo dataset:

```bash
python -m scripts.seed
```

Demo accounts (password from `SEED_PASSWORD`, default `DemoPassw0rd!`):

| Email | Role |
| --- | --- |
| `ada@example.com` | admin |
| `grace@example.com` | member |
| `linus@example.com` | member |

The seed is idempotent: rerunning it creates nothing new.

### 5. Explore

```bash
# Log in
curl -s -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"ada@example.com","password":"DemoPassw0rd!"}'

# Use the returned access_token
curl -s http://127.0.0.1:8000/api/v1/projects \
  -H "Authorization: Bearer $ACCESS_TOKEN"

# Download the first project's report
curl -s -o report.pdf \
  "http://127.0.0.1:8000/api/v1/projects/$PROJECT_ID/report.pdf" \
  -H "Authorization: Bearer $ACCESS_TOKEN"
```

## Docker

The repository includes a multi-stage Dockerfile (non-root UID 10001, read-only
filesystem in Compose, health check against `/health/ready`) and a Compose stack
with PostgreSQL 17.

```bash
cp .env.example .env        # optional; Compose has development defaults
# Set a real SECRET_KEY before exposing the stack.
docker compose up --build -d
docker compose logs -f api
```

On start the container applies migrations (`RUN_MIGRATIONS=true` by default) and
optionally seeds demo data (`RUN_SEED=true`). API: <http://localhost:8000/docs>.

```bash
docker compose down          # stop
docker compose down -v       # stop and delete the database volume
```

The Compose `SECRET_KEY` fallback is a clearly labelled development value. Override it.

## Configuration

All settings are environment variables (case-insensitive), optionally loaded from `.env`.
See [`.env.example`](.env.example) for the annotated list.

| Variable | Default | Description |
| --- | --- | --- |
| `APP_NAME` | `Enterprise API` | OpenAPI title and branding |
| `APP_VERSION` | `1.0.0` | Reported version |
| `ENVIRONMENT` | `development` | `development` / `staging` / `production` / `testing` |
| `DEBUG` | `false` | Debug mode |
| `API_V1_PREFIX` | `/api/v1` | Versioned API mount point |
| `LOG_LEVEL` | `INFO` | Root log level |
| `DOCS_ENABLED` | `true` | Serve `/docs`, `/redoc`, `/openapi.json` |
| `SECRET_KEY` | insecure dev value | JWT signing key; **must be ≥32 chars in production** |
| `JWT_ALGORITHM` | `HS256` | `HS256` / `HS384` / `HS512` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | Access token lifetime (1–1440) |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token lifetime (1–90) |
| `BCRYPT_ROUNDS` | `12` | Password hashing cost (4–16) |
| `DATABASE_URL` | PostgreSQL DSN | `postgresql+asyncpg://...` or `sqlite+aiosqlite:///...` |
| `DB_ECHO` | `false` | Echo SQL |
| `DB_POOL_SIZE` | `10` | PostgreSQL pool size |
| `DB_MAX_OVERFLOW` | `20` | PostgreSQL pool overflow |
| `AUTO_CREATE_TABLES` | `true` | Create tables at startup; must be `false` in production |
| `CORS_ORIGINS` | localhost:3000/5173 | Comma-separated; `*` rejected because credentials are enabled |
| `TRUSTED_HOSTS` | `*` | Comma-separated `Host` allowlist |
| `MAX_REQUEST_BODY_BYTES` | `1048576` | Request body cap (1 MiB) |
| `DEFAULT_PAGE_SIZE` | `20` | Default page size |
| `MAX_PAGE_SIZE` | `100` | Maximum page size |
| `RATE_LIMIT_ENABLED` | `true` | Enable sliding-window limits |
| `AUTH_RATE_LIMIT_PER_MINUTE` | `20` | Auth endpoint limit per client IP |
| `PDF_RATE_LIMIT_PER_MINUTE` | `10` | PDF endpoint limit per client IP |
| `PDF_MAX_TASKS` | `500` | Tasks included per report |
| `SEED_PASSWORD` | `DemoPassw0rd!` | Demo account password (seed script only) |

Production refuses to boot when `SECRET_KEY` is missing/short or
`AUTO_CREATE_TABLES=true` — a deliberate fail-closed guard.

## Authentication flow

```
register/login ──► { user, tokens: { access_token, refresh_token } }
                          │
       Authorization: Bearer <access_token>  ──► protected endpoints
                          │
 access token expires ──► POST /auth/refresh { refresh_token }
                          │
                    new access + refresh pair (old refresh is revoked)
```

Rules worth knowing:

- Refresh tokens are stored only as SHA-256 hashes; the plaintext is returned once.
- Refresh rotation is single-use. Replaying an already-revoked token revokes **all**
  sessions for that account and bumps `token_version`.
- `POST /auth/logout-all`, `PUT /users/me/password`, account deletion, admin role
  changes, and deactivation all invalidate existing access tokens immediately.
- Deactivated accounts receive `401 account_disabled` on login and on token use.
- Passwords require 8–128 characters with at least one letter and one digit.

## API overview

Base path: `/api/v1` except health probes. Full request/response schemas are in
[`docs/API.md`](docs/API.md), [`docs/openapi.json`](docs/openapi.json), and `/docs`.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/auth/register` | Create an account and receive tokens |
| `POST` | `/auth/login` | JSON login |
| `POST` | `/auth/token` | OAuth2 password flow (Swagger) |
| `POST` | `/auth/refresh` | Rotate a refresh token |
| `POST` | `/auth/logout` | Revoke one or all refresh tokens |
| `POST` | `/auth/logout-all` | Revoke everything and invalidate access tokens |
| `GET` | `/auth/me` | Current user (alias of `/users/me`) |
| `GET` | `/users/me` | Current profile |
| `PATCH` | `/users/me` | Update email / full name |
| `PUT` | `/users/me/password` | Change password (revokes sessions) |
| `DELETE` | `/users/me` | Delete account (password + `confirm`) |
| `GET` | `/projects` | List visible projects |
| `POST` | `/projects` | Create a project |
| `GET` | `/projects/{id}` | Project detail |
| `PATCH` | `/projects/{id}` | Update project |
| `DELETE` | `/projects/{id}` | Delete project (owner) |
| `GET` | `/projects/{id}/stats` | Task statistics |
| `GET` | `/projects/{id}/members` | List members |
| `POST` | `/projects/{id}/members` | Add member by email (owner) |
| `PATCH` | `/projects/{id}/members/{user_id}` | Change role (owner) |
| `DELETE` | `/projects/{id}/members/{user_id}` | Remove member / leave |
| `GET` | `/projects/{id}/report.pdf` | Download PDF report |
| `GET` | `/projects/{id}/tasks` | List tasks |
| `POST` | `/projects/{id}/tasks` | Create task |
| `POST` | `/projects/{id}/tasks/bulk-status` | Bulk status update |
| `GET` | `/projects/{id}/tasks/{task_id}` | Task detail |
| `PATCH` | `/projects/{id}/tasks/{task_id}` | Update task |
| `DELETE` | `/projects/{id}/tasks/{task_id}` | Delete task |
| `GET` | `/admin/users` | List/search users (admin) |
| `PATCH` | `/admin/users/{id}` | Change role/status (admin) |
| `GET` | `/admin/stats` | Platform counters (admin) |
| `GET` | `/health/live` | Liveness |
| `GET` | `/health/ready` | Readiness (database) |
| `GET` | `/health` | Alias for readiness |

### Error envelope

Every failure returns the same shape:

```json
{
  "error": {
    "code": "insufficient_project_role",
    "message": "You do not have permission to perform this action",
    "details": [],
    "request_id": "0f4b7c1a2d3e4f5a6b7c8d9e0f1a2b3c"
  }
}
```

The `request_id` matches the `X-Request-ID` response header and the server log line.
Validation errors also include a per-field `details` list. See
[`docs/API.md`](docs/API.md#error-catalog) for the full error-code catalog.

### Pagination

Collection endpoints accept `page` (1-based) and `per_page` and return:

```json
{
  "items": [],
  "total": 0,
  "page": 1,
  "per_page": 20,
  "pages": 0,
  "has_next": false,
  "has_prev": false
}
```

## Examples

### Register and create a project

```bash
BASE=http://127.0.0.1:8000/api/v1

curl -s -X POST "$BASE/auth/register" \
  -H 'Content-Type: application/json' \
  -d '{"email":"ada@example.com","password":"CorrectHorse9","full_name":"Ada Lovelace"}'

TOKEN=$(curl -s -X POST "$BASE/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"email":"ada@example.com","password":"CorrectHorse9"}' \
  | python -c 'import json,sys; print(json.load(sys.stdin)["tokens"]["access_token"])')

curl -s -X POST "$BASE/projects" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"Apollo Migration","description":"Move billing to events","status":"active"}'
```

### Add a member and create a task

```bash
PROJECT_ID=...

curl -s -X POST "$BASE/projects/$PROJECT_ID/members" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"email":"grace@example.com","role":"editor"}'

curl -s -X POST "$BASE/projects/$PROJECT_ID/tasks" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
        "title":"Design the event contract",
        "priority":"critical",
        "labels":["design","kafka"],
        "due_date":"2026-11-15"
      }'
```

### Filter and paginate

```bash
curl -s "$BASE/projects/$PROJECT_ID/tasks?status=in_progress&priority=critical&page=1&per_page=20" \
  -H "Authorization: Bearer $TOKEN"
```

### Refresh

```bash
curl -s -X POST "$BASE/auth/refresh" \
  -H 'Content-Type: application/json' \
  -d '{"refresh_token":"<refresh_token>"}'
```

## PDF reports

`GET /projects/{id}/report.pdf?include_completed=true` returns a real PDF with:

- Project name, status, owner, dates, and description.
- Totals, per-status and per-priority breakdowns, completion rate, overdue count.
- Task table (status, priority, assignee, due date, labels), ordered by workflow state.
- Team roster with roles.
- Generation timestamp and the requesting user.

Response headers:

```
Content-Type: application/pdf
Content-Disposition: attachment; filename="apollo-migration-report-20261025.pdf"
Content-Length: ...
Cache-Control: no-store
```

`include_completed=false` excludes `done` tasks. Reports larger than `PDF_MAX_TASKS`
are truncated and clearly marked. Rendering uses a worker thread, and the route is
rate limited to protect CPU.

## Project layout

```
.
├── app/
│   ├── api/
│   │   ├── deps.py            # auth, pagination, project-role guards
│   │   ├── errors.py          # error envelope + exception handlers
│   │   ├── middleware.py      # request ids, security headers, body limit, rate limit
│   │   └── v1/                # auth, users, projects, tasks, admin, health, router
│   ├── core/
│   │   ├── config.py          # pydantic-settings + production validators
│   │   ├── exceptions.py      # typed domain errors
│   │   ├── logging.py         # request-id-aware logging
│   │   ├── pagination.py      # Page[T] envelope
│   │   ├── ratelimit.py       # sliding-window limiter
│   │   ├── security.py        # JWT + bcrypt helpers
│   │   └── timeutils.py       # UTC normalisation
│   ├── db/                    # async engine/session
│   ├── models/                # User, RefreshToken, Project, ProjectMember, Task
│   ├── schemas/               # Pydantic request/response models
│   ├── services/              # business logic (auth, users, projects, tasks, pdf)
│   └── main.py                # app factory, middleware, lifespan
├── docker/                    # container entrypoint
├── docs/                      # API, architecture, deployment, openapi.json
├── migrations/                # Alembic environment + versions
├── scripts/                   # seed + OpenAPI export
├── tests/                     # 123 tests across API, system, and unit layers
├── Dockerfile
├── docker-compose.yml
├── Makefile
└── pyproject.toml
```

The request path is strictly layered:
`router → dependency guards → service → SQLAlchemy model`. Routers never contain
business rules, and services never build HTTP responses. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Database & migrations

Schema objects: `users`, `refresh_tokens`, `projects`, `project_members`, `tasks`.

```bash
alembic upgrade head            # apply
alembic downgrade -1            # roll back one revision
alembic revision --autogenerate -m "add something"
alembic current
alembic history
```

Enums are stored as `VARCHAR` with `CHECK` constraints (`native_enum=False`) rather
than PostgreSQL `ENUM` types. This keeps migrations reversible and portable, and
avoids the `ALTER TYPE ... ADD VALUE` transaction restrictions. UUID primary keys and
`CURRENT_TIMESTAMP` defaults are portable between PostgreSQL and SQLite.

`AUTO_CREATE_TABLES=true` is a convenience for local development. Production must
use Alembic; the settings validator enforces this.

## Testing

```bash
pytest -q                 # full suite
pytest tests/test_auth.py -q
ruff check .              # lint
ruff format --check .     # format gate
```

The suite covers authentication and token lifecycle, users, projects and membership
authorization, tasks and bulk updates, reports, admin safeguards, middleware/system
behaviour (health, request ids, limits, security headers), and pure unit tests for
security, pagination, and rate limiting.

Tests use a file-based SQLite database per test, with `Bcrypt` rounds reduced and rate
limiting disabled. They exercise the real ASGI app through `httpx.ASGITransport`, so
routing, dependencies, middleware, serialization, and error handling are all real.
PostgreSQL-specific migration and seed behaviour is validated in CI.

## Security notes

- Passwords: bcrypt with a configurable cost; strength validated at the schema layer.
- Tokens: signed JWTs with `type`, `jti`, `iat`, `exp`, and `ver` claims. Access and
  refresh tokens are not interchangeable.
- Refresh tokens: hashed at rest, single-use, with reuse detection that revokes the
  entire session family.
- Enumeration resistance: login returns a generic `invalid_credentials`; non-members
  get `404` for projects they cannot see.
- Authorization: project roles are resolved in one query per request; admin bypass is
  explicit; owner-only operations are enforced in the service layer, not just the route.
- Transport: set `TRUSTED_HOSTS` and run behind TLS. HSTS is emitted only when
  `ENVIRONMENT=production`. Security headers are applied to every response, including
  synthesized 500s.
- Input limits: 1 MiB body cap, bounded page sizes, bounded label counts and lengths,
  bounded bulk updates.
- Rate limiting: in-process sliding window keyed by client IP. It is per worker — for
  multi-instance deployments, move the limiter to Redis (see
  [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md#scaling-the-rate-limiter)).
- Logging: request IDs correlate client reports to server logs; secrets and tokens are
  never logged.

Report vulnerabilities per [`SECURITY.md`](SECURITY.md), not in public issues.

## Documentation

- [`docs/API.md`](docs/API.md) — endpoints, payloads, status codes, error catalog.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — layering, auth internals, data model,
  middleware order, trade-offs.
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — Docker, reverse proxy, migrations,
  scaling, backups, observability.
- [`docs/openapi.json`](docs/openapi.json) — generated schema, committed for review and
  client generation. Regenerate with `make openapi`.
- [`CHANGELOG.md`](CHANGELOG.md), [`CONTRIBUTING.md`](CONTRIBUTING.md),
  [`SECURITY.md`](SECURITY.md).

## Contributing

Issues and pull requests are welcome. Please read [`CONTRIBUTING.md`](CONTRIBUTING.md)
first; the short version is: branch, add tests, run `ruff check .`,
`ruff format --check .`, and `pytest -q`, then open a PR.

## License

MIT — see [`LICENSE`](LICENSE).
