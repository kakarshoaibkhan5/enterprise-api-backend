# Architecture

This document explains how the service is put together, why the boundaries fall
where they do, and where the known trade-offs are. It is written for engineers who
will operate or extend the codebase, not just call it.

---

## Contents

- [Design goals](#design-goals)
- [Layering](#layering)
- [Request lifecycle](#request-lifecycle)
- [Middleware order](#middleware-order)
- [Configuration](#configuration)
- [Data model](#data-model)
- [Authentication internals](#authentication-internals)
- [Authorization and tenancy](#authorization-and-tenancy)
- [Pagination, filtering, sorting](#pagination-filtering-sorting)
- [PDF generation](#pdf-generation)
- [Error handling](#error-handling)
- [Concurrency and transactions](#concurrency-and-transactions)
- [Trade-offs and known limits](#trade-offs-and-known-limits)

---

## Design goals

1. **One obvious place for each concern.** HTTP concerns live in routers, business
   rules in services, persistence in models. A reviewer should never wonder whether a
   permission check is enforced in the route or the service.
2. **Fail closed.** Production refuses to boot with an insecure secret or
   auto-created tables. Ambiguous authorization returns a denial, not a guess.
3. **No information leaks.** Non-members get `404` for foreign projects. Login is
   generic. Refresh tokens are stored as hashes.
4. **Predictable contracts.** One error envelope, one pagination envelope, one
   timestamp format, one auth flow.
5. **Boring, portable infrastructure.** SQLAlchemy 2.x, Alembic, PostgreSQL, a
   multi-stage image. Nothing that requires a proprietary runtime.

---

## Layering

```
HTTP request
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ Middleware        CORS → TrustedHost → SecurityHeaders →      │
│                   RequestContext → BodySizeLimit → RateLimit  │
└──────────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ Routers (app/api/v1/*)                                       │
│  • declare paths, status codes, response models              │
│  • resolve dependencies (session, current user, project ctx) │
│  • call exactly one service function                         │
│  • never mutate ORM objects directly                         │
└──────────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ Dependencies (app/api/deps.py)                               │
│  • bearer token → User                                       │
│  • user + project_id → ProjectContext (with role guard)      │
│  • pagination parameters                                     │
└──────────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ Services (app/services/*)                                    │
│  • all business rules and authorization decisions            │
│  • transaction boundaries (commit/rollback)                  │
│  • raise typed AppError subclasses                           │
│  • return ORM objects or Pydantic models, never Responses    │
└──────────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ Models (app/models/*) + async session (app/db/*)             │
│  • SQLAlchemy 2.x declarative mappings                       │
│  • portable types, constraints, and indexes                  │
└──────────────────────────────────────────────────────────────┘
```

### Why routers stay thin

A router's job is translation: HTTP in, HTTP out. If authorization lived in routers,
every new route would be a chance to forget a check. Instead, `ProjectEditor` and
`ProjectOwner` are *typed dependencies*: a route that declares `ProjectEditor` cannot
be written without the guard running. The guard itself delegates to
`resolve_project_access()` in the service layer, so the rule exists in exactly one
place.

### Why services own commits

Each service function is a transaction boundary. This keeps routers free of
`await db.commit()` calls, makes rollback behaviour uniform, and allows a service to
perform several writes atomically (for example: create a project *and* its owner
membership).

---

## Request lifecycle

1. **Uvicorn** accepts the connection and calls the ASGI app.
2. **CORS** handles pre-flight requests and adds response headers.
3. **TrustedHost** (only when `TRUSTED_HOSTS` is not `*`) rejects unknown `Host`
   headers.
4. **SecurityHeaders** wraps the response with hardening headers, including HSTS in
   production.
5. **RequestContext** reads or generates `X-Request-ID`, stores it in a
   `ContextVar`, starts a timer, and guarantees an error envelope for exceptions
   that escape the framework.
6. **BodySizeLimit** rejects requests whose `Content-Length` exceeds the cap.
7. **RateLimit** applies the sliding window to auth and report routes.
8. **FastAPI routing** resolves path, query, and body parameters.
9. **Dependencies** load the session, authenticate the bearer token, and resolve the
   project role.
10. **Router** calls a service function.
11. **Service** queries or mutates the database, commits, and returns domain objects.
12. **Response model** serializes the result; FastAPI validates the output against
    the declared schema.
13. **Middleware unwinds**, adding the request ID, security headers, rate-limit
    headers, and a structured log line.

---

## Middleware order

`add_middleware` in Starlette prepends, so the **last** middleware added is the
**outermost**. The code adds them in this order:

| Added | Effective position | Middleware |
| --- | --- | --- |
| 1st | innermost | `RateLimitMiddleware` |
| 2nd | | `BodySizeLimitMiddleware` |
| 3rd | | `RequestContextMiddleware` |
| 4th | | `SecurityHeadersMiddleware` |
| 5th (conditional) | | `TrustedHostMiddleware` |
| 6th | outermost | `CORSMiddleware` |

Resulting request flow: **CORS → TrustedHost → SecurityHeaders → RequestContext →
BodySizeLimit → RateLimit → app**.

Two placements are deliberate:

- **SecurityHeaders outside RequestContext** means the synthesized `500` response
  produced by RequestContext still carries `X-Content-Type-Options`,
  `X-Frame-Options`, and friends.
- **RequestContext outside BodySizeLimit and RateLimit** means requests rejected for
  size or rate still receive an `X-Request-ID` and a matching error envelope.

---

## Configuration

`app/core/config.py` defines a single `Settings` model backed by
`pydantic-settings`. Precedence is environment variable → `.env` file → default.

`get_settings()` is wrapped in `lru_cache`, so the process parses configuration once.
Modules that need settings call `get_settings()` at import time. This is safe because
configuration is immutable for the lifetime of a process; changing it requires a
restart.

### Validation

| Rule | Enforced by |
| --- | --- |
| `SECRET_KEY` ≥ 32 chars and not the dev default in production | `model_validator` |
| `AUTO_CREATE_TABLES` must be `false` in production | `model_validator` |
| `CORS_ORIGINS` may not contain `*` | `field_validator` |
| Numeric ranges (TTL, pools, page sizes, rounds) | `Field(ge=..., le=...)` |
| `API_V1_PREFIX` starts with `/` | `field_validator` |

Comma-separated lists use `Annotated[..., NoDecode]` so the validator receives the raw
string rather than having pydantic-settings attempt JSON parsing.

---

## Data model

```
users
  id (uuid, pk)          email (unique)         hashed_password
  role (member|admin)    is_active              token_version
  full_name              last_login_at          created_at, updated_at
    │
    ├──< refresh_tokens (user_id)          hashed token, expiry, revoked_at, UA, IP
    │
    ├──< projects (owner_id)               name, description, status, dates
    │       │
    │       ├──< project_members (project_id, user_id unique)
    │       │        role (owner|editor|viewer), joined_at
    │       │
    │       └──< tasks (project_id)
    │                title, description, status, priority, labels[json]
    │                assignee_id → users, created_by_id → users
    │                due_date, completed_at
    │
    └──< project_members (user_id)         membership rows for joined projects
```

### Cardinality and deletion

| Relationship | On delete |
| --- | --- |
| `users → projects` | `CASCADE` (deleting an account deletes owned projects) |
| `users → refresh_tokens` | `CASCADE` |
| `users → project_members` | `CASCADE` |
| `projects → project_members` | `CASCADE` |
| `projects → tasks` | `CASCADE` |
| `users → tasks.assignee_id` | `SET NULL` |
| `users → tasks.created_by_id` | `SET NULL` |

`passive_deletes=True` on the collections lets the database perform cascades instead
of SQLAlchemy loading every child row first.

### Portability choices

- **Enums** are stored as `VARCHAR` with a `CHECK` constraint
  (`native_enum=False`). PostgreSQL `ENUM` types cannot have values removed, are
  awkward inside transactions, and make `alembic downgrade` painful.
- **UUIDs** use SQLAlchemy's `Uuid` type, which maps to native `uuid` on PostgreSQL
  and `CHAR(32)` on SQLite.
- **Timestamps** use `CURRENT_TIMESTAMP` server defaults rather than
  `now()`/`datetime('now')`, so the same DDL works on both engines.
- **Booleans** use `text("true")`, accepted by PostgreSQL and SQLite.
- **Labels** use `JSON` with a `JSONB` variant on PostgreSQL, giving indexable binary
  JSON in production without breaking SQLite tests.

### Indexes

Indexes are chosen from the actual query shapes:

| Index | Supports |
| --- | --- |
| `ix_users_email` (unique) | login lookup |
| `ix_users_role` | admin filtering |
| `ix_projects_owner_created` | "my projects, newest first" |
| `ix_projects_status` | status filter |
| `ix_project_members_project_id` | membership listing |
| `ix_project_members_user_id` | "projects I belong to" |
| `ix_tasks_project_status` | the most common task filter |
| `ix_tasks_project_priority` | priority filter |
| `ix_tasks_project_created` | default sort |
| `ix_tasks_project_due_date` | due-date sort and overdue scan |
| `ix_tasks_assignee_id` | "assigned to me" |

---

## Authentication internals

### Token shape

Both token types are HS256 (configurable to HS384/HS512) JWTs with the same claims:

| Claim | Meaning |
| --- | --- |
| `sub` | user UUID |
| `type` | `access` or `refresh` |
| `jti` | random hex, unique per issuance |
| `ver` | snapshot of `users.token_version` |
| `iat` | issued-at |
| `exp` | expiry |

`decode_token()` requires *all* of `exp`, `iat`, `sub`, `type`, `ver`, `jti`
(`options={"require": [...]}` on PyJWT), then asserts the expected `type`. This makes
it impossible to use an access token as a refresh token or vice versa.

### Access tokens are stateless, with a version escape hatch

Verifying an access token is a signature check plus one indexed user lookup to compare
`ver`. There is no denylist and no per-request token table scan. When an event must
invalidate sessions immediately, the service increments `users.token_version`:

| Event | Bumps `ver`? | Revokes refresh tokens? |
| --- | --- | --- |
| `logout` with a refresh token | No | Only that token |
| `logout` without a body | No | All |
| `logout-all` | Yes | All |
| Password change | Yes | All |
| Account deletion | Row removed | Cascade |
| Admin deactivation | Yes | All |
| Admin role change | Yes | All |
| Refresh-token reuse detected | Yes | All |

The trade-off is one user row read per authenticated request. At this scale that is a
cheap, cache-friendly primary-key lookup, and it buys immediate revocation without
building a distributed denylist.

### Refresh tokens are stateful and single-use

Refresh tokens are JWTs too, but the database is the source of truth:

1. The plaintext token is returned to the client exactly once.
2. Only `SHA-256(token)` is stored in `refresh_tokens.token_hash` (unique index).
3. On refresh, the service decodes the JWT, looks up the hash, and checks
   `revoked_at`, `expires_at`, `users.is_active`, and the `ver` claim.
4. The old row is revoked with a **conditional UPDATE**:
   `WHERE id = :id AND revoked_at IS NULL`.
5. If the update affects zero rows, another request already consumed the token. That
   is treated as theft: all refresh tokens are revoked and `token_version` is bumped.

Step 4 is what makes rotation safe under concurrency. Two simultaneous refresh calls
with the same token cannot both succeed, even with multiple workers, because the
database serializes the conditional update.

### Password hashing

`bcrypt` with a configurable cost (`BCRYPT_ROUNDS`, default 12). Verification catches
`ValueError`/`TypeError` and returns `False`, so a malformed stored hash cannot turn a
login attempt into a 500. Password strength (8–128 characters, at least one letter and
one digit) is enforced at the schema layer before hashing.

---

## Authorization and tenancy

### Resolution order

`resolve_project_access(db, project_id, user, min_role)` performs, in order:

1. Load the project by id. Missing → `404 project_not_found`.
2. If `user.role == admin` → grant `owner` on any project.
3. If `project.owner_id == user.id` → grant `owner`.
4. Load the user's `ProjectMember` row.
   - Missing → `404 project_not_found` (not `403`).
   - Role below `min_role` → `403 insufficient_project_role`.
5. Return a `ProjectContext(project, role, user)`.

The `ProjectContext` travels into the service so authorization is not re-derived from
scratch. Services that need to distinguish "owner" from "admin acting as owner" still
have the original `user`.

### Why `404` for non-members

Returning `403` for a project the caller cannot see confirms that the id exists. In a
multi-tenant system that is an enumeration oracle. `404` makes "does not exist" and
"not yours" indistinguishable, at the cost of a slightly surprising status code for
legitimate users. The error code remains `project_not_found`.

### Role matrix

| Action | viewer | editor | owner | admin |
| --- | --- | --- | --- | --- |
| Read project, tasks, members, stats, report | ✅ | ✅ | ✅ | ✅ |
| Update project | ❌ | ✅ | ✅ | ✅ |
| Create / update / delete tasks, bulk status | ❌ | ✅ | ✅ | ✅ |
| Add / update / remove members | ❌ | ❌ | ✅ | ✅ |
| Delete project | ❌ | ❌ | ✅ | ✅ |

Ownership is `projects.owner_id`, not a membership role. The owner also receives a
`project_members` row with `role=owner`, which keeps listing queries uniform. That row
is immutable: changing it returns `400 owner_role_immutable`, and removing it returns
`400 owner_cannot_leave`. Ownership transfer is deliberately not exposed.

### Admin semantics

`role=admin` is a platform role, not a project role. An admin is treated as the
`owner` of every project but does not appear in `project_members`. This keeps
"who is on this team" honest while granting operational access. Admin actions that
change another account's role or activation bump that account's token version, and
self-demotion/self-deactivation is blocked (`400 self_lockout_prevented`) as is
removing the final active admin (`409 last_admin_protected`).

---

## Pagination, filtering, sorting

`PageParams` computes `offset` and `limit` from `page`/`per_page`, with bounds
enforced by `Query(ge=..., le=...)`. `Page[T]` adds `total`, `pages`, `has_next`, and
`has_prev` so clients do not have to compute them.

Every list endpoint follows the same pattern:

1. Build a `select()` with the base filters.
2. Apply the project scope (`owner_id == user.id` OR a membership exists).
3. Apply optional filters (status, priority, assignee, search).
4. Count with `select(func.count()).select_from(subquery)`.
5. Apply `order_by`, `offset`, `limit`.
6. Eager-load relationships needed by the response (`selectinload`/`joined`).

Search uses `ILIKE`/`lower() LIKE` on the name and description. Sort fields are
resolved through an explicit allowlist mapping, never by interpolating user input into
SQL.

---

## PDF generation

PDF rendering is the only CPU-bound operation in the request path, so it is split into
two phases:

1. **Data phase (async).** The route loads the project, its tasks (bounded by
   `PDF_MAX_TASKS`), and its members, then builds a primitive `ReportData` dataclass.
   No ORM objects cross the thread boundary.
2. **Render phase (thread pool).** `run_in_threadpool(render_report, data)` executes
   ReportLab, which is synchronous and CPU-bound. This keeps the event loop free to
   serve other requests.

The result is a `bytes` buffer streamed with `Content-Disposition: attachment`,
`Content-Length`, and `Cache-Control: no-store`. Filenames are slugified from the
project name with a `YYYYMMDD` suffix.

Truncation is explicit: if a project has more tasks than `PDF_MAX_TASKS`, the report
includes the first N in workflow order and sets `ReportData.truncated`, which the
renderer surfaces in the document. The endpoint is rate limited because PDF rendering
is the cheapest way to burn CPU.

---

## Error handling

There are three layers:

1. **Typed domain errors** (`app/core/exceptions.py`): `AppError` subclasses carry a
   status code, a stable machine code, a human message, optional details, and optional
   headers. Services raise these; they never build responses.
2. **Registered handlers** (`app/api/errors.py`): map `AppError`,
   `RequestValidationError`, `HTTPException`, `IntegrityError`, and bare `Exception`
   into the canonical envelope.
3. **Middleware fallback** (`RequestContextMiddleware`): if an exception escapes
   before a response starts, it emits the same envelope so the client never sees
   Starlette's HTML error page.

The envelope:

```json
{
  "error": {
    "code": "insufficient_project_role",
    "message": "You do not have permission to perform this action",
    "details": [],
    "request_id": "0f4b7c1a..."
  }
}
```

`request_id` is the same value returned in `X-Request-ID` and written to the log line,
which is what makes a user report actionable.

---

## Concurrency and transactions

- **One session per request**, provided by `get_db()`. It is rolled back automatically
  if an exception propagates.
- **`expire_on_commit=False`** so returned ORM objects remain usable after commit
  without triggering lazy loads in the serializer.
- **`autoflush=False`** so queries do not unexpectedly flush partial state.
- **Explicit re-fetch after write.** Create/update services re-query with eager loads
  before returning, because `async` SQLAlchemy cannot lazy-load attributes during
  response serialization.
- **Conditional updates for race-prone invariants**: refresh rotation and bulk status
  updates use `UPDATE ... WHERE <state still as expected>` and inspect `rowcount`.
- **Pool settings** (`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `pool_recycle=1800`) are tuned
  for long-lived PostgreSQL connections behind a pooler; `pool_pre_ping=True` avoids
  handing out dead connections after a database restart.

---

## Trade-offs and known limits

| Decision | Benefit | Cost / limit |
| --- | --- | --- |
| Stateless access tokens + `ver` check | Immediate revocation, no denylist | One user lookup per request |
| `404` for non-members | No tenant enumeration | Slightly surprising for legitimate users |
| Owner is `owner_id`, not a mutable role | Simple, unambiguous ownership | No ownership transfer endpoint |
| In-process rate limiter | Zero dependencies, good enough for one process | Each worker/replica has its own budget |
| `VARCHAR` + `CHECK` enums | Portable, reversible migrations | No native enum ordering or type safety at the DB |
| Page-based pagination | Simple for UIs, stable counts | Offset scans get slower at very large offsets |
| Synchronous ReportLab | Mature, deterministic output | CPU-bound; bounded and rate limited |
| Admin bypasses project roles | Operational access, no lockouts | Admins can read all tenant data by design |
| Demo seed in the image | Fast onboarding | Must stay opt-in (`RUN_SEED=false` by default) |

Items that would be addressed before very large scale: cursor pagination for deep
offsets, a Redis-backed rate limiter, refresh-token pruning, and a read replica for
reporting queries. None of them change the public contract.
