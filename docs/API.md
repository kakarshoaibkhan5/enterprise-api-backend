# API Reference

Base URL: `http://localhost:8000`

| Environment | URL |
| --- | --- |
| Local | `http://127.0.0.1:8000` |
| Docker Compose | `http://localhost:8000` |

Versioned endpoints live under `/api/v1`. Health probes are mounted at `/health`.
Interactive documentation is served at `/docs` (Swagger UI) and `/redoc`.

A generated, machine-readable copy of the schema lives in [`openapi.json`](openapi.json).
Regenerate it with:

```bash
python -m scripts.export_openapi
# or: make openapi
```

---

## Contents

- [Conventions](#conventions)
- [Authentication](#authentication)
- [Roles & permissions](#roles--permissions)
- [Endpoints](#endpoints)
  - [Authentication](#authentication-endpoints)
  - [Users](#users)
  - [Projects](#projects)
  - [Members](#members)
  - [Tasks](#tasks)
  - [Reports](#reports)
  - [Administration](#administration)
  - [Health](#health)
- [Error catalog](#error-catalog)
- [Rate limiting](#rate-limiting)
- [Changelog of the contract](#changelog-of-the-contract)

---

## Conventions

### Request headers

| Header | Required | Notes |
| --- | --- | --- |
| `Authorization: Bearer <access_token>` | Protected routes | JWT access token |
| `Content-Type: application/json` | JSON bodies | Form encoding only on `/auth/token` |
| `X-Request-ID` | Optional | Echoed back; generated when absent |

### Response headers

| Header | When | Notes |
| --- | --- | --- |
| `X-Request-ID` | Every response | Correlates with logs and error envelopes |
| `X-Content-Type-Options` | Every response | `nosniff` |
| `X-Frame-Options` | Every response | `DENY` |
| `Referrer-Policy` | Every response | `no-referrer` |
| `Permissions-Policy` | Every response | Restricts browser features |
| `Content-Security-Policy` | Docs routes | Restricts script/style sources |
| `Strict-Transport-Security` | Production only | `max-age=63072000; includeSubDomains` |
| `Retry-After` | `429` | Seconds until the window resets |
| `X-RateLimit-Limit` | `429` | Requests allowed per window |

### Timestamps

All timestamps are ISO 8601 UTC with an explicit `Z`/`+00:00` offset. Dates without a
time component use `YYYY-MM-DD` (project `start_date`/`due_date`, task `due_date`).

### Pagination

Every collection endpoint accepts `page` (default `1`, minimum `1`) and `per_page`
(default from `DEFAULT_PAGE_SIZE`, maximum `MAX_PAGE_SIZE`). Responses use the `Page[T]`
envelope:

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

`pages` is `0` when there are no results. `has_next` is false on the last page.

### Error envelope

All errors — including `404` from an unknown route — use:

```json
{
  "error": {
    "code": "validation_error",
    "message": "The request body or parameters failed validation",
    "details": [
      {
        "field": "password",
        "message": "Password must contain at least one letter and one digit",
        "type": "value_error"
      }
    ],
    "request_id": "5d9f0c1b2a3e4f5a6b7c8d9e0f1a2b3c"
  }
}
```

`details` is always present (an empty list when there is nothing to add). Validation
errors populate it per field; the rate limiter adds `{"retry_after": n}`.

---

## Authentication

### Token model

| Token | Default TTL | Storage server-side | Use |
| --- | --- | --- | --- |
| Access | 30 minutes | Stateless JWT | `Authorization: Bearer ...` |
| Refresh | 7 days | SHA-256 hash in `refresh_tokens` | `/auth/refresh` |

Access-token claims:

```json
{
  "sub": "<user uuid>",
  "type": "access",
  "jti": "<uuid>",
  "ver": 3,
  "iat": "2026-10-25T10:00:00+00:00",
  "exp": "2026-10-25T10:30:00+00:00"
}
```

`ver` must match the account's current `token_version`. Bumping it invalidates every
access token immediately — this is how `logout-all`, password changes, deactivation,
role changes, and deletion enforce revocation without a denylist.

Refresh tokens are opaque JWTs with `type=refresh`. They are single-use: each refresh
rotation revokes the presented token and issues a new pair. Presenting a revoked token
is treated as theft and revokes **all** sessions for that account.

### Authentication endpoints

#### `POST /api/v1/auth/register`

Create an account and receive tokens in one round trip.

Request:

```json
{
  "email": "ada@example.com",
  "password": "CorrectHorse9",
  "full_name": "Ada Lovelace"
}
```

Rules: `email` must be valid and unique (case-insensitive); `password` is 8–128
characters and must contain at least one letter and one digit; `full_name` is optional
and whitespace-collapsed.

Response `201 Created`:

```json
{
  "user": {
    "id": "0d1f...",
    "email": "ada@example.com",
    "full_name": "Ada Lovelace",
    "role": "member",
    "is_active": true,
    "created_at": "2026-10-25T09:59:00+00:00",
    "updated_at": "2026-10-25T09:59:00+00:00",
    "last_login_at": null
  },
  "tokens": {
    "access_token": "eyJ...",
    "refresh_token": "eyJ...",
    "token_type": "bearer",
    "expires_in": 1800,
    "refresh_expires_in": 604800
  }
}
```

Errors: `409 email_already_registered`, `422 validation_error`.

#### `POST /api/v1/auth/login`

Request:

```json
{ "email": "ada@example.com", "password": "CorrectHorse9" }
```

Response `200 OK`: same `AuthResponse` shape as registration, with `last_login_at`
updated.

Errors: `401 invalid_credentials`, `401 account_disabled`, `422 validation_error`,
`429 rate_limit_exceeded`.

#### `POST /api/v1/auth/token`

OAuth2 password grant, form-encoded. Used by the Swagger **Authorize** dialog.
Fields: `username` (the email), `password`, optional `grant_type=password`,
`scope`.

Response `200 OK`:

```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 1800
}
```

#### `POST /api/v1/auth/refresh`

Request:

```json
{ "refresh_token": "eyJ..." }
```

Response `200 OK`: `AuthResponse` with a **new** access/refresh pair.

Errors:

| Status | Code | Meaning |
| --- | --- | --- |
| `401` | `invalid_refresh_token` | Malformed, unknown, or wrong-type token |
| `401` | `refresh_token_expired` | Past expiry (the record is now revoked) |
| `401` | `refresh_token_reuse` | Token already rotated — all sessions revoked |
| `401` | `account_disabled` | Account deactivated since issuance |
| `429` | `rate_limit_exceeded` | Too many refresh attempts |

#### `POST /api/v1/auth/logout`

Requires an access token. Optional body:

```json
{ "refresh_token": "eyJ..." }
```

- With `refresh_token`: revokes that single token.
- Without a body (or with `refresh_token: null`): revokes every refresh token for the
  account. Existing access tokens remain valid until they expire.

Response `200 OK`: `{ "message": "Logged out" }`.

#### `POST /api/v1/auth/logout-all`

Requires an access token. Revokes all refresh tokens **and** bumps `token_version`, so
every outstanding access token starts returning `401 token_revoked` immediately.

Response `200 OK`: `{ "message": "All sessions have been terminated" }`.

#### `GET /api/v1/auth/me`

Alias for `GET /users/me`. Returns the `UserRead` object.

---

## Users

#### `GET /api/v1/users/me`

Returns the current profile:

```json
{
  "id": "0d1f...",
  "email": "ada@example.com",
  "full_name": "Ada Lovelace",
  "role": "member",
  "is_active": true,
  "created_at": "2026-10-25T09:59:00+00:00",
  "updated_at": "2026-10-25T09:59:00+00:00",
  "last_login_at": "2026-10-25T10:00:00+00:00"
}
```

#### `PATCH /api/v1/users/me`

Partial update. Both fields are optional:

```json
{ "email": "ada@analytical.engine", "full_name": "Ada King" }
```

- `email` is normalised to lowercase and must remain unique.
- `full_name` is whitespace-collapsed; a blank value becomes `null`.

Response `200 OK`: `UserRead`.

Errors: `409 email_already_registered`, `422 validation_error`.

#### `PUT /api/v1/users/me/password`

```json
{
  "current_password": "CorrectHorse9",
  "new_password": "CorrectHorse10"
}
```

On success, every refresh token is revoked and `token_version` is bumped. All access
tokens, including the caller's, become invalid.

Response `200 OK`:

```json
{ "message": "Password updated. All sessions have been revoked; please sign in again." }
```

Errors: `400 invalid_password`, `400 password_unchanged`, `422 validation_error`.

#### `DELETE /api/v1/users/me`

```json
{ "password": "CorrectHorse9", "confirm": true }
```

Permanently deletes the account and cascades to owned projects, memberships, tasks,
and refresh tokens. `confirm` must be `true`.

Response `200 OK`: `{ "message": "Account deleted" }`.

Errors: `400 not_confirmed`, `400 invalid_password`.

---

## Roles & permissions

### Platform roles

| Role | Capabilities |
| --- | --- |
| `member` | Everything below, scoped to owned and joined projects |
| `admin` | Everything a member can do, plus `/admin/*`; treated as project **owner** on every project |

### Project roles

| Role | Read | Edit project | Manage tasks | Manage members | Delete project |
| --- | --- | --- | --- | --- | --- |
| `viewer` | ✅ | ❌ | ❌ | ❌ | ❌ |
| `editor` | ✅ | ✅ | ✅ | ❌ | ❌ |
| `owner` | ✅ | ✅ | ✅ | ✅ | ✅ |

Ownership is `projects.owner_id`; the owner also has an `owner` membership row.
Invitations and role updates cannot grant `owner` (rejected at `422`). Ownership cannot
be transferred through the API.

Authorization rules:

- Non-members receive **`404 project_not_found`**, never `403`, so project existence
  is not leaked across tenants.
- Members with an insufficient role receive **`403 insufficient_project_role`**.
- Owner-only actions use **`403 owner_required`**.
- The owner's own membership cannot be changed (`400 owner_role_immutable`) or removed
  (`400 owner_cannot_leave`).

---

## Projects

#### `GET /api/v1/projects`

Query parameters:

| Name | Type | Default | Notes |
| --- | --- | --- | --- |
| `page` | int | `1` | ≥ 1 |
| `per_page` | int | `20` | ≤ `MAX_PAGE_SIZE` |
| `search` | str | — | Case-insensitive fragment of name or description (≤120 chars) |
| `status` | enum | — | `planning`, `active`, `on_hold`, `completed`, `archived` |
| `sort` | enum | `created_at` | `created_at`, `updated_at`, `name`, `due_date`, `status` |
| `order` | enum | `desc` | `asc`, `desc` |

Admins see every project. Other users see projects they own plus projects where they
hold a membership. Each item includes contextual `role`, `member_count`, `task_count`.

Response `200 OK`: `Page[ProjectRead]`.

#### `POST /api/v1/projects`

```json
{
  "name": "Apollo Migration",
  "description": "Move billing to the event-driven platform",
  "status": "active",
  "start_date": "2026-10-01",
  "due_date": "2027-03-31"
}
```

The creator becomes owner and receives an owner membership.

Response `201 Created`: `ProjectRead`.

Errors: `422 validation_error` (blank name, `due_date` before `start_date`, invalid
status).

#### `GET /api/v1/projects/{project_id}`

Requires `viewer`. Response `200 OK`: `ProjectRead`.

#### `PATCH /api/v1/projects/{project_id}`

Requires `editor`. All fields optional; only supplied fields change.

```json
{ "status": "on_hold", "due_date": "2027-04-30" }
```

Response `200 OK`: `ProjectRead`. Errors: `403 insufficient_project_role`,
`422 validation_error`.

#### `DELETE /api/v1/projects/{project_id}`

Requires `owner`. Cascades to memberships and tasks.

Response `204 No Content`. Errors: `403 owner_required`, `404 project_not_found`.

#### `GET /api/v1/projects/{project_id}/stats`

Requires `viewer`.

```json
{
  "project_id": "5f0c...",
  "total_tasks": 12,
  "by_status": { "todo": 4, "in_progress": 3, "blocked": 1, "done": 4 },
  "by_priority": { "low": 2, "medium": 5, "high": 3, "critical": 2 },
  "completed_tasks": 4,
  "overdue_tasks": 1,
  "completion_rate": 33.33
}
```

Counters include zero-valued keys for all statuses and priorities.

---

## Members

#### `GET /api/v1/projects/{project_id}/members`

Requires `viewer`. Returns an array (not paginated) of:

```json
[
  {
    "id": "e1a2...",
    "project_id": "5f0c...",
    "user_id": "0d1f...",
    "email": "ada@example.com",
    "full_name": "Ada Lovelace",
    "role": "owner",
    "joined_at": "2026-10-25T10:00:00+00:00"
  }
]
```

#### `POST /api/v1/projects/{project_id}/members`

Requires `owner`.

```json
{ "email": "grace@example.com", "role": "editor" }
```

`role` defaults to `viewer` and must be `editor` or `viewer`.

Response `201 Created`: `ProjectMemberRead`.

Errors:

| Status | Code | Meaning |
| --- | --- | --- |
| `404` | `user_not_found` | No account with that email |
| `400` | `account_disabled` | Target account is deactivated |
| `409` | `already_member` | Target already belongs to the project |
| `403` | `owner_required` | Caller is not the owner |
| `422` | `validation_error` | `role` was `owner` or the email was invalid |

#### `PATCH /api/v1/projects/{project_id}/members/{user_id}`

Requires `owner`.

```json
{ "role": "viewer" }
```

Response `200 OK`: `ProjectMemberRead`.

Errors: `400 owner_role_immutable`, `404 member_not_found`, `403 owner_required`,
`422 validation_error`.

#### `DELETE /api/v1/projects/{project_id}/members/{user_id}`

- The owner may remove any member except themselves.
- A non-owner member may remove themselves (leave).
- The owner cannot leave; delete the project instead.

Response `200 OK`: `{ "message": "Member removed" }`.

Errors: `400 owner_cannot_leave`, `403 owner_required`, `404 member_not_found`.

---

## Tasks

Tasks are nested under projects: `/api/v1/projects/{project_id}/tasks`.

Fields:

| Field | Type | Constraints |
| --- | --- | --- |
| `title` | string | 1–200 chars, whitespace-collapsed |
| `description` | string \| null | ≤ 10,000 chars |
| `status` | enum | `todo`, `in_progress`, `blocked`, `done` (default `todo`) |
| `priority` | enum | `low`, `medium`, `high`, `critical` (default `medium`) |
| `labels` | string[] | ≤ 20 unique labels, each ≤ 40 chars, case-insensitive dedupe |
| `assignee_id` | uuid \| null | Must be a project member or the owner |
| `due_date` | date \| null | `YYYY-MM-DD` |
| `completed_at` | timestamp \| null | Managed by the server |

#### `GET /api/v1/projects/{project_id}/tasks`

Requires `viewer`.

| Name | Type | Default | Notes |
| --- | --- | --- | --- |
| `page` / `per_page` | int | `1` / `20` | Pagination |
| `search` | str | — | Fragment of title or description |
| `status` | enum | — | Task status |
| `priority` | enum | — | Task priority |
| `assignee_id` | uuid | — | Filter by assignee |
| `sort` | enum | `created_at` | `created_at`, `updated_at`, `due_date`, `title`, `priority`, `status` |
| `order` | enum | `desc` | `asc`, `desc` |

Response `200 OK`: `Page[TaskRead]`.

```json
{
  "items": [
    {
      "id": "8b2c...",
      "project_id": "5f0c...",
      "title": "Design the event contract",
      "description": "Publish versioned schemas for invoice events.",
      "status": "in_progress",
      "priority": "critical",
      "labels": ["design", "kafka"],
      "assignee": {
        "id": "0d1f...",
        "email": "ada@example.com",
        "full_name": "Ada Lovelace"
      },
      "created_by": {
        "id": "0d1f...",
        "email": "ada@example.com",
        "full_name": "Ada Lovelace"
      },
      "due_date": "2026-11-15",
      "completed_at": null,
      "created_at": "2026-10-25T10:00:00+00:00",
      "updated_at": "2026-10-25T10:05:00+00:00"
    }
  ],
  "total": 1,
  "page": 1,
  "per_page": 20,
  "pages": 1,
  "has_next": false,
  "has_prev": false
}
```

#### `POST /api/v1/projects/{project_id}/tasks`

Requires `editor`.

```json
{
  "title": "Design the event contract",
  "description": "Publish versioned schemas for invoice and payment events.",
  "status": "in_progress",
  "priority": "critical",
  "labels": ["design", "kafka"],
  "assignee_id": "0d1f...",
  "due_date": "2026-11-15"
}
```

Response `201 Created`: `TaskRead`.

Errors: `422 validation_error` when the assignee is not a project member, when labels
exceed limits, or when fields are malformed.

#### `POST /api/v1/projects/{project_id}/tasks/bulk-status`

Requires `editor`.

```json
{
  "task_ids": ["8b2c...", "9c3d..."],
  "status": "done"
}
```

Constraints: 1–100 ids. Ids outside this project are ignored. Entering `done` sets
`completed_at`; leaving `done` clears it.

Response `200 OK`:

```json
{ "updated": 2, "task_ids": ["8b2c...", "9c3d..."] }
```

Errors: `404 task_not_found` when none of the supplied ids belong to the project,
`422 validation_error` for an empty or oversized list.

#### `GET /api/v1/projects/{project_id}/tasks/{task_id}`

Requires `viewer`. Response `200 OK`: `TaskRead`. Errors: `404 task_not_found`.

#### `PATCH /api/v1/projects/{project_id}/tasks/{task_id}`

Requires `editor`. All fields optional.

```json
{ "status": "done", "assignee_id": null }
```

Passing `assignee_id: null` unassigns the task. Moving into `done` stamps
`completed_at`; moving out clears it.

Response `200 OK`: `TaskRead`.

#### `DELETE /api/v1/projects/{project_id}/tasks/{task_id}`

Requires `editor`. Response `204 No Content`.

---

## Reports

#### `GET /api/v1/projects/{project_id}/report.pdf`

Requires `viewer`.

| Query | Type | Default | Notes |
| --- | --- | --- | --- |
| `include_completed` | bool | `true` | Set `false` to omit `done` tasks |

Response `200 OK`:

```
Content-Type: application/pdf
Content-Disposition: attachment; filename="apollo-migration-report-20261025.pdf"
Content-Length: 48213
Cache-Control: no-store
```

The document contains project metadata, task statistics, a task table ordered by
workflow state and due date, and the member roster. Reports larger than `PDF_MAX_TASKS`
are truncated and labelled accordingly.

Errors: `404 project_not_found`, `429 rate_limit_exceeded`.

---

## Administration

All `/admin/*` routes require the `admin` role. Non-admins receive
`403 admin_required`.

#### `GET /api/v1/admin/users`

| Query | Type | Notes |
| --- | --- | --- |
| `page` / `per_page` | int | Pagination |
| `search` | str | Email or full-name fragment (≤120 chars) |
| `role` | enum | `member`, `admin` |
| `is_active` | bool | Filter by activation state |

Response `200 OK`: `Page[UserRead]`.

#### `PATCH /api/v1/admin/users/{user_id}`

```json
{ "role": "admin", "is_active": true, "full_name": "Ada King" }
```

Changing `role` or `is_active` bumps the target's `token_version` and revokes their
refresh tokens, so the change takes effect immediately.

Safeguards:

| Status | Code | Meaning |
| --- | --- | --- |
| `400` | `self_lockout_prevented` | An admin tried to demote/deactivate themselves |
| `409` | `last_admin_protected` | The change would leave no active administrator |
| `404` | `user_not_found` | Unknown user id |

Response `200 OK`: `UserRead`.

#### `GET /api/v1/admin/stats`

```json
{
  "total_users": 42,
  "active_users": 40,
  "admin_users": 3,
  "total_projects": 17,
  "total_tasks": 260
}
```

---

## Health

Health routes are **not** under `/api/v1` and require no authentication.

| Route | Checks | Failure |
| --- | --- | --- |
| `GET /health/live` | Process only | Never fails while serving |
| `GET /health/ready` | `SELECT 1` against the database | `503` with `status: "degraded"` |
| `GET /health` | Alias for readiness | Same as above |

Readiness response:

```json
{
  "status": "ok",
  "version": "1.0.0",
  "environment": "production",
  "database": "up"
}
```

Liveness response: `{ "status": "ok" }`.

Use `/health/live` for restart decisions and `/health/ready` for load-balancer
membership. A database outage should not restart the process, only remove it from
rotation.

---

## Error catalog

### By status

| Status | Default code | Notes |
| --- | --- | --- |
| `400` | `bad_request` | Semantically invalid operation |
| `401` | `authentication_required` | Missing/invalid/revoked credentials; `WWW-Authenticate: Bearer` |
| `403` | `permission_denied` | Authenticated but not allowed |
| `404` | `not_found` | Unknown route or resource |
| `405` | `method_not_allowed` | Wrong HTTP method |
| `406` | `not_acceptable` | Unacceptable `Accept` header |
| `409` | `conflict` | Uniqueness or state conflict |
| `413` | `payload_too_large` | Body exceeded `MAX_REQUEST_BODY_BYTES` |
| `415` | `unsupported_media_type` | Wrong content type |
| `422` | `validation_error` | Schema/field validation failed |
| `429` | `rate_limit_exceeded` | Sliding-window limit hit |
| `500` | `internal_error` | Unhandled server error (quote the request id) |
| `503` | `service_unavailable` | Dependency unavailable |

### Application codes

| Code | Status | Typical cause |
| --- | --- | --- |
| `validation_error` | 422 | Invalid body, query, or path parameter |
| `invalid_credentials` | 401 | Wrong email or password |
| `invalid_token` | 401 | Malformed/expired access token or wrong token type |
| `token_revoked` | 401 | `ver` mismatch after password change, logout-all, deactivation, or role change |
| `account_disabled` | 401 | Deactivated account on login or token use |
| `invalid_refresh_token` | 401 | Unknown, malformed, or wrong-type refresh token |
| `refresh_token_expired` | 401 | Refresh token past expiry |
| `refresh_token_reuse` | 401 | Already-rotated refresh token replayed; all sessions revoked |
| `email_already_registered` | 409 | Registration or profile email collision |
| `invalid_password` | 400 | Wrong current password |
| `password_unchanged` | 400 | New password equals the current one |
| `not_confirmed` | 400 | Account deletion without `confirm: true` |
| `admin_required` | 403 | Non-admin calling `/admin/*` |
| `self_lockout_prevented` | 400 | Admin demoting/deactivating themselves |
| `last_admin_protected` | 409 | Change would remove the final active admin |
| `user_not_found` | 404 | Unknown user id or invited email |
| `project_not_found` | 404 | Unknown project **or** caller is not a member |
| `insufficient_project_role` | 403 | Membership role below the required level |
| `owner_required` | 403 | Owner-only operation attempted by another role |
| `owner_role_immutable` | 400 | Attempt to change the owner's membership role |
| `owner_cannot_leave` | 400 | Owner attempting to remove themselves |
| `member_not_found` | 404 | Membership not found |
| `already_member` | 409 | User already belongs to the project |
| `task_not_found` | 404 | Task not in this project (or no bulk matches) |
| `rate_limit_exceeded` | 429 | Too many requests in the current window |
| `payload_too_large` | 413 | Request body over the limit |
| `internal_error` | 500 | Unexpected failure; logs contain the same `request_id` |

---

## Rate limiting

Sliding-window limits, keyed by client IP (respecting `X-Forwarded-For` only if the
process is configured to trust proxy headers):

| Scope | Default | Env var |
| --- | --- | --- |
| `/api/v1/auth/*` | 20 requests / 60 s | `AUTH_RATE_LIMIT_PER_MINUTE` |
| `*/report.pdf` | 10 requests / 60 s | `PDF_RATE_LIMIT_PER_MINUTE` |

On rejection:

```
HTTP/1.1 429 Too Many Requests
Retry-After: 37
X-RateLimit-Limit: 20
X-Request-ID: ...
```

```json
{
  "error": {
    "code": "rate_limit_exceeded",
    "message": "Too many requests; please retry later",
    "details": [{ "retry_after": 37 }],
    "request_id": "..."
  }
}
```

The limiter is **per process**. Behind multiple workers or replicas, each process
enforces its own budget; move the limiter to Redis before relying on it as a hard
security control. See [`DEPLOYMENT.md`](DEPLOYMENT.md#scaling-the-rate-limiter).

---

## Changelog of the contract

| Version | Change |
| --- | --- |
| `1.0.0` | Initial public contract: auth, users, projects, members, tasks, reports, admin, health |
