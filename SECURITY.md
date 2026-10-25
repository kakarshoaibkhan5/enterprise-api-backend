# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| `1.x` | ✅ |
| `< 1.0` | ❌ |

Security fixes are applied to the latest `1.x` release. If you run a fork, you own the
patching.

---

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Report privately through either channel:

1. GitHub's **Report a vulnerability** button on the repository's *Security* tab
   (preferred — it creates a private advisory).
2. Email the maintainers at `security@example.com` with the subject
   `[enterprise-api-backend] vulnerability`.

Please include:

- A description of the issue and the affected component or endpoint.
- Reproduction steps or a proof of concept (requests, payloads, configuration).
- The impact you believe it has (confidentiality, integrity, availability).
- The version or commit you tested.
- Any suggested remediation.

Redact real credentials, tokens, and personal data from your report.

### What to expect

| Stage | Target |
| --- | --- |
| Acknowledgement | 3 business days |
| Initial assessment and severity | 10 business days |
| Fix or mitigation plan for critical/high | 30 days |
| Public disclosure | Coordinated, after a fix ships |

We will keep you informed, credit you in the advisory unless you prefer otherwise, and
coordinate disclosure timing with you. Please give us a reasonable window before
publishing.

### Out of scope

- Findings from automated scanners with no demonstrated impact.
- Missing headers on endpoints that do not serve HTML (report them anyway if cheap to
  fix, but they are low priority).
- Denial of service from unauthenticated traffic without a concrete amplification
  vector — rate limiting is documented as per-process, and hard limits belong at the
  edge.
- Issues that require a compromised host, database access, or `SECRET_KEY`; those are
  treated as "game over" by design.
- Social engineering of maintainers or users.
- Vulnerabilities in dependencies without a plausible path through this application —
  report those upstream, though we welcome a heads-up.

---

## Security model

Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design. The short
version:

### Authentication

- Passwords are hashed with **bcrypt** (configurable cost, default 12). Plaintext is
  never stored or logged.
- Access and refresh tokens are JWTs signed with `SECRET_KEY` (HS256 by default),
  carrying a required `type`, `jti`, `iat`, `exp`, and `ver` claim.
- Token types are not interchangeable: an access token cannot be used to refresh.
- Access tokens are short-lived (default 30 minutes). `users.token_version` is compared
  on every request, so bumping it invalidates all outstanding access tokens
  immediately.
- Refresh tokens are stored **only as SHA-256 hashes**, are single-use, and rotate on
  every refresh. Replaying a revoked token is treated as theft: all sessions are
  revoked and the token version is bumped.

### Authorization

- Platform roles: `member`, `admin`.
- Project roles: `owner`, `editor`, `viewer`, resolved per request from
  `project_members` (or `projects.owner_id`).
- Non-members receive `404`, never `403`, so project existence is not leaked across
  tenants.
- Owner-only operations are enforced in the service layer, not only in the route.
- Admin actions that change a user's role or activation immediately revoke that user's
  sessions.

### Input and transport

- Pydantic v2 validation on every request body, query, and path parameter, with
  bounded lengths and enumerated values.
- Request bodies are capped (`MAX_REQUEST_BODY_BYTES`, default 1 MiB) by middleware;
  configure the same cap at the reverse proxy for chunked requests.
- Page sizes, label counts, bulk-update sizes, and report sizes are bounded.
- CORS allows credentials, so wildcard origins are rejected at startup.
- Security headers on every response: `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`, plus HSTS in
  production and a CSP on documentation routes.
- `TRUSTED_HOSTS` rejects unexpected `Host` headers when configured.

### Rate limiting

Sliding-window limits are applied to authentication and PDF endpoints. **The limiter is
per process.** With multiple workers or replicas the effective limit multiplies. For
hard guarantees, enforce limits at the gateway or move the limiter to Redis — see
[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md#scaling-the-rate-limiter).

### Fail-closed configuration

In `ENVIRONMENT=production` the app refuses to start if:

- `SECRET_KEY` is the development default or shorter than 32 characters;
- `AUTO_CREATE_TABLES=true` (Alembic must own the schema).

### Logging

Logs include request ids, method, path, status, and duration. They never include
passwords, tokens, or `Authorization` headers. A `refresh_token_reuse` warning is a
genuine security signal and is worth alerting on.

---

## Operator responsibilities

The application cannot secure a deployment by itself. You must:

- Generate a strong, unique `SECRET_KEY` and store it in a secret manager.
- Run behind TLS 1.2+; never expose Uvicorn directly to the internet.
- Set `TRUSTED_HOSTS` and `CORS_ORIGINS` to real values.
- Use a least-privilege database role and restrict network access to the database.
- Keep dependencies patched (`pip list --outdated`, Dependabot, or your SCA tool).
- Ship logs off-host with retention and alert on 5xx and token-reuse events.
- Back up the database and rehearse restores.
- Enable `--proxy-headers` only behind a proxy you control; otherwise clients can
  spoof their IP and evade limits.

## Dependency security

The runtime dependency set is intentionally small. When adding a dependency, prefer:

- actively maintained projects with a security policy,
- pure-Python or widely packaged wheels,
- permissive licenses compatible with MIT.

Pin upper bounds in `pyproject.toml` (already the convention) and review lockfile
changes.
