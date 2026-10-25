# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

Nothing yet. Planned work that does not change the public contract:

- Redis-backed rate limiter for multi-worker and multi-replica deployments.
- Cursor-based pagination for very large collections.
- Scheduled pruning of expired and revoked refresh tokens.
- Ownership transfer endpoint.

---

## [1.0.0] - 2026-10-25

First public release. The API contract in this version is documented in
[`docs/API.md`](docs/API.md) and frozen into [`docs/openapi.json`](docs/openapi.json).

### Added

#### Authentication and accounts

- Registration, login (JSON and OAuth2 password form), logout, logout-all, and
  refresh-token rotation under `/api/v1/auth`.
- JWT access and refresh token pairs with `sub`, `type`, `jti`, `iat`, `exp`, and
  `ver` claims, signed with HS256 (HS384/HS512 selectable).
- Refresh tokens stored as SHA-256 hashes, single-use, rotated on each refresh, with
  reuse detection that revokes every session and bumps the user's token version.
- Immediate access-token revocation via `users.token_version` (no denylist required).
- Password change (invalidates all sessions) and self-service account deletion gated
  on `confirm=true` plus the current password.
- `GET /api/v1/auth/me` and `PATCH /api/v1/auth/me` for profile reads and updates.

#### Projects and tenancy

- Project CRUD with status lifecycle (`planning`, `active`, `on_hold`, `completed`,
  `archived`), target dates, and bounded descriptions.
- Multi-tenant isolation through `project_members` with `owner`, `editor`, and
  `viewer` roles.
- Membership management: list, add by email, change role, remove. Ownership is
  immutable (`owner_role_immutable`, `owner_cannot_leave`).
- Per-project statistics endpoint (task counts by status, priority, and assignment,
  plus overdue and completion metrics).
- Project search, status filtering, and sortable listings.

#### Tasks

- Task CRUD scoped to a project with title, description, status
  (`todo`, `in_progress`, `blocked`, `done`), priority
  (`low`, `medium`, `high`, `critical`), labels, due date, and assignee.
- Assignee validation that requires project membership.
- `completed_at` set and cleared automatically with status transitions.
- Bulk status update with per-id authorization and bounded batch size.
- Filtering by status, priority, assignee, and free-text search, plus sort allowlist.

#### PDF reporting

- `GET /api/v1/projects/{id}/report.pdf` generating an A4 project report with summary,
  task table, and member list.
- Report rendering isolated from ORM objects behind a primitive `ReportData` model and
  executed in a threadpool so the event loop is never blocked.
- Explicit truncation to `PDF_MAX_TASKS` tasks with the truncation surfaced in the
  document.
- Streamed attachment responses with `Content-Disposition`, `Content-Length`, and
  `Cache-Control: no-store`.

#### Administration

- Admin-only user listing with pagination and search.
- Admin role and activation changes that immediately revoke the target's sessions.
- Guards against self-demotion, self-deactivation (`self_lockout_prevented`), and
  removing the final active admin (`last_admin_protected`).

#### Platform

- Single error envelope
  `{"error": {"code", "message", "details", "request_id"}}` for domain errors,
  validation failures, `HTTPException`, integrity errors, and unhandled exceptions.
- Page-based pagination envelope with `items`, `total`, `page`, `per_page`, `pages`,
  `has_next`, and `has_prev`.
- Structured single-line logging with a per-request `X-Request-ID` also echoed in
  error envelopes.
- Middleware stack: CORS, optional TrustedHost, security headers (HSTS in production,
  CSP on docs routes), request context, body-size limit, and rate limiting.
- In-process sliding-window rate limiting on authentication and PDF endpoints, with
  `429 rate_limit_exceeded` and `Retry-After`.
- Health probes: `/health/live` (process only) and `/health/ready` (`SELECT 1`).
- Swagger UI, ReDoc, and raw OpenAPI schema with an OAuth2 password-flow security
  scheme wired for interactive "Authorize" in the docs.
- Fail-closed production validators: insecure or short `SECRET_KEY` and
  `AUTO_CREATE_TABLES=true` refuse to boot; `CORS_ORIGINS` rejects `*`.

#### Persistence and migrations

- Async SQLAlchemy 2.x with `asyncpg` for PostgreSQL and `aiosqlite` for local tests.
- Initial Alembic revision covering all four tables, indexes, and constraints, with a
  reversible `downgrade()` verified in CI.
- Portable DDL (`CURRENT_TIMESTAMP`, `text("true")`, `VARCHAR` + `CHECK` enums) so the
  same migrations run on PostgreSQL and SQLite.
- `JSONB` labels on PostgreSQL with a plain `JSON` fallback elsewhere.
- Idempotent demo seed (`python -m scripts.seed`) — safe to run repeatedly.

#### Tooling and delivery

- Multi-stage Dockerfile on `python:3.12-slim`, running as non-root UID/GID 10001 with
  a health check against `/health/ready`.
- Entrypoint that optionally runs migrations (`RUN_MIGRATIONS`) and seeding
  (`RUN_SEED`), then `exec`s the server.
- `docker-compose.yml` wiring PostgreSQL 17 and the API with a read-only root
  filesystem, dropped capabilities, `no-new-privileges`, and a `tmpfs` for `/tmp`.
- GitHub Actions pipeline: lint/format, tests on Python 3.11–3.13, PostgreSQL
  migrations (upgrade → downgrade base → upgrade head) plus seed idempotency and a live
  smoke test, and a Docker build.
- `Makefile` targets for install, run, test, lint, format, check, migrate, revision,
  downgrade, seed, OpenAPI export, and Compose.
- `scripts/export_openapi.py` producing the committed `docs/openapi.json`.
- 123 tests covering authentication, authorization, tenancy isolation, validation,
  pagination, reporting, admin guards, and error envelopes.

#### Documentation

- `README.md` with features, quickstart, Docker usage, configuration reference, auth
  flow, endpoint table, and worked examples.
- `docs/API.md` with conventions, the full endpoint contract, role matrix, error
  catalog, rate-limit behaviour, and a contract changelog.
- `docs/ARCHITECTURE.md` covering layering, request lifecycle, middleware ordering,
  data model, token internals, tenancy, concurrency, and trade-offs.
- `docs/DEPLOYMENT.md` covering containers, reverse proxy and TLS, migrations,
  rate-limiter scaling, backups, observability, Kubernetes, and a production checklist.
- `CONTRIBUTING.md`, `SECURITY.md`, `LICENSE` (MIT), `.env.example`, and this changelog.

### Security

- Passwords hashed with bcrypt at a configurable cost (default 12), with constant-time
  verification that cannot fail open on a malformed stored hash.
- Non-members receive `404 project_not_found` rather than `403`, preventing project-id
  enumeration across tenants.
- Login is generic (`invalid_credentials`) so it does not reveal whether an email is
  registered.
- Token type confusion is rejected: an access token cannot be used to refresh.
- Admin role or activation changes bump the target's `token_version` immediately.
- No tokens, passwords, or `Authorization` headers are written to logs.
- Body size capped at 1 MiB by default, with the same limit expected at the proxy for
  chunked requests.

### Known limitations

- The rate limiter is **per process**. With multiple workers or replicas the effective
  limit multiplies; use Redis or an edge gateway for hard guarantees. Documented in
  [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md#scaling-the-rate-limiter).
- Ownership transfer is not exposed; `projects.owner_id` is fixed at creation.
- Pagination is offset-based, which slows at very deep offsets.
- Refresh-token pruning is manual; expired rows are ignored but not deleted
  automatically.
- SQLite is supported for development and tests only, not as a production database.

---

[Unreleased]: https://github.com/your-org/enterprise-api-backend/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/your-org/enterprise-api-backend/releases/tag/v1.0.0
