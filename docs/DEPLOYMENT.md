# Deployment Guide

Everything needed to run this service outside a laptop: container images, Compose,
environment hardening, reverse proxy and TLS, migrations, scaling, backups, and
observability.

---

## Contents

- [Runtime requirements](#runtime-requirements)
- [Environment variables](#environment-variables)
- [Docker image](#docker-image)
- [Docker Compose](#docker-compose)
- [Manual / bare-metal deployment](#manual--bare-metal-deployment)
- [Reverse proxy and TLS](#reverse-proxy-and-tls)
- [Database migrations](#database-migrations)
- [Scaling the rate limiter](#scaling-the-rate-limiter)
- [Scaling the application](#scaling-the-application)
- [Backups and recovery](#backups-and-recovery)
- [Observability](#observability)
- [Kubernetes notes](#kubernetes-notes)
- [Production checklist](#production-checklist)

---

## Runtime requirements

| Component | Minimum | Notes |
| --- | --- | --- |
| Python | 3.11 | CI tests 3.11, 3.12, 3.13. Image uses 3.12. |
| PostgreSQL | 14 | 17 recommended and used in Compose/CI. |
| CPU | 1 vCPU | PDF rendering is bursty; 2+ recommended. |
| Memory | 512 MiB | 1 GiB comfortable with 2 workers. |
| Disk | 500 MiB | Image + logs; the database lives separately. |

SQLite is supported for local development and tests only. It serializes writes and
does not provide the concurrency the API assumes.

---

## Environment variables

The full annotated list is in [`.env.example`](../.env.example). The settings the
deployment *must* get right:

| Variable | Production requirement |
| --- | --- |
| `SECRET_KEY` | Random, ≥ 32 characters, unique per environment. **Generate it.** |
| `ENVIRONMENT` | `production` (enables HSTS and the fail-closed validators) |
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@host:5432/db` |
| `AUTO_CREATE_TABLES` | `false` — Alembic owns the schema |
| `CORS_ORIGINS` | Exact browser origins; `*` is rejected |
| `TRUSTED_HOSTS` | Your public hostname(s), e.g. `api.example.com` |
| `DOCS_ENABLED` | `false` if the API is not meant to be publicly discoverable |
| `BCRYPT_ROUNDS` | `12` (raise only after measuring login latency) |
| `WEB_CONCURRENCY` | `2` per vCPU is a reasonable start |

Generate a secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

The application **refuses to start** when `ENVIRONMENT=production` and either the
secret is the development default / shorter than 32 characters, or
`AUTO_CREATE_TABLES=true`. That is intentional: a misconfigured production boot
should fail loudly at deploy time, not silently weaken the service.

### Secrets handling

Never bake secrets into the image or commit a populated `.env`. Options, in order of
preference:

1. Your orchestrator's secret store (Kubernetes `Secret`, ECS `secrets`, Docker
   `secret`, Vault).
2. A mounted file read by your process manager.
3. Environment variables injected at runtime.

Rotating `SECRET_KEY` invalidates every outstanding access and refresh token at once.
Plan the rotation as a forced re-login for all users.

---

## Docker image

The `Dockerfile` is a two-stage build:

- **Builder** (`python:3.12-slim`): creates `/opt/venv` and installs
  `.[postgres]`. Build tools, caches, and headers stay here.
- **Runtime** (`python:3.12-slim`): copies only `/opt/venv`, application code,
  migrations, scripts, and the entrypoint. Runs as UID/GID **10001**, has no shell
  login, and contains no compiler.

```bash
docker build -t enterprise-api-backend:1.0.0 .
docker run --rm -p 8000:8000 \
  -e ENVIRONMENT=production \
  -e SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
  -e DATABASE_URL="postgresql+asyncpg://user:pass@host.docker.internal:5432/enterprise_api" \
  -e AUTO_CREATE_TABLES=false \
  -e TRUSTED_HOSTS=localhost \
  enterprise-api-backend:1.0.0
```

### Entrypoint behaviour

`docker/docker-entrypoint.sh` runs before the server:

| Variable | Default | Effect |
| --- | --- | --- |
| `RUN_MIGRATIONS` | `true` | Runs `alembic upgrade head` |
| `RUN_SEED` | `false` | Runs `python -m scripts.seed` |

Then it `exec`s the CMD, so the API process becomes PID 1 and receives signals
correctly.

> **Running migrations from every replica is a race.** For a single-container
> deployment the entrypoint behaviour is convenient. For multi-replica rollouts set
> `RUN_MIGRATIONS=false` on the application and run migrations as a separate job (see
> [Database migrations](#database-migrations)).

### Health check

The image declares a `HEALTHCHECK` against `/health/ready`, which executes `SELECT 1`
against the database. `/health/live` deliberately does **not** touch the database, so
a database outage removes instances from load-balancer rotation without triggering
restart loops.

---

## Docker Compose

`docker-compose.yml` provides PostgreSQL 17 plus the API, wired for a realistic local
run: production-style environment, migrations on start, read-only root filesystem,
`no-new-privileges`, all capabilities dropped, and a `tmpfs` for `/tmp`.

```bash
cp .env.example .env      # optional
docker compose up --build -d
docker compose logs -f api
curl -fsS http://localhost:8000/health/ready
```

Override the development defaults before exposing the stack:

```bash
cat > .env <<'EOF'
SECRET_KEY=<paste a generated secret>
POSTGRES_PASSWORD=<strong password>
ENVIRONMENT=production
TRUSTED_HOSTS=localhost,127.0.0.1
DOCS_ENABLED=false
RUN_SEED=false
EOF
docker compose up -d
```

Load demo data once:

```bash
RUN_SEED=true docker compose up -d api
```

Tear down:

```bash
docker compose down        # keep the database volume
docker compose down -v     # delete the database volume
```

> Compose is a **development and demo** topology. It publishes PostgreSQL on the host
> and uses a development `SECRET_KEY` fallback. Do not treat it as a production
> blueprint without overriding both.

---

## Manual / bare-metal deployment

```bash
python3 -m venv /opt/enterprise-api/venv
source /opt/enterprise-api/venv/bin/activate
pip install ".[postgres]"

export ENVIRONMENT=production
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export DATABASE_URL="postgresql+asyncpg://user:pass@db:5432/enterprise_api"
export AUTO_CREATE_TABLES=false
export TRUSTED_HOSTS=api.example.com

alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --workers 4
```

`--proxy-headers` makes Uvicorn honour `X-Forwarded-For` and `X-Forwarded-Proto` from
a trusted proxy. Only enable it behind a proxy you control; otherwise clients can spoof
their source address and evade IP rate limits.

Example systemd unit:

```ini
[Unit]
Description=Enterprise API
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=enterprise
Group=enterprise
WorkingDirectory=/opt/enterprise-api
EnvironmentFile=/etc/enterprise-api.env
ExecStart=/opt/enterprise-api/venv/bin/uvicorn app.main:app \
  --host 127.0.0.1 --port 8000 --proxy-headers --workers 4
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/enterprise-api

[Install]
WantedBy=multi-user.target
```

Bind to `127.0.0.1` and let nginx/Caddy terminate TLS.

---

## Reverse proxy and TLS

Terminate TLS at the proxy, forward plain HTTP to the app, and set the headers the app
expects.

### nginx

```nginx
upstream enterprise_api {
    server 127.0.0.1:8000;
    keepalive 32;
}

server {
    listen 80;
    server_name api.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name api.example.com;

    ssl_certificate     /etc/letsencrypt/live/api.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.example.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;

    # Match MAX_REQUEST_BODY_BYTES. nginx rejects chunked overflows too.
    client_max_body_size 1m;

    location / {
        proxy_pass         http://enterprise_api;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
        proxy_buffering    off;   # streams PDFs without buffering to disk
    }

    location /health/live {
        proxy_pass http://enterprise_api;
        access_log off;
    }
}
```

Notes:

- `client_max_body_size` is the real bound on chunked uploads; the application's
  `BodySizeLimitMiddleware` can only inspect `Content-Length`.
- `proxy_buffering off` keeps PDF responses streaming.
- Keep `X-Forwarded-For` intact so the rate limiter sees the real client. Only trust
  the proxy: `--proxy-headers` must never be enabled on an internet-facing socket.

### Caddy

```
api.example.com {
    encode gzip
    reverse_proxy 127.0.0.1:8000 {
        header_up X-Forwarded-Proto {scheme}
    }
    request_body {
        max_size 1MB
    }
}
```

Caddy provisions certificates automatically. The app still emits HSTS when
`ENVIRONMENT=production`; do not duplicate it with a conflicting `max-age`.

### TLS checklist

- TLS 1.2+ only; disable TLS 1.0/1.1 and legacy ciphers.
- HSTS comes from the app in production (`max-age=31536000; includeSubDomains`). Only
  enable `preload` after you are certain every subdomain is HTTPS.
- Renew certificates automatically and alert on expiry.

---

## Database migrations

Alembic is the only supported way to change the production schema. The initial
revision is `migrations/versions/9e62853e8f9f_initial_schema.py`.

```bash
alembic upgrade head        # apply everything
alembic current             # show the applied revision
alembic history             # list revisions
alembic downgrade -1        # roll back one revision
alembic downgrade base      # empty the schema (destructive)
```

### Recommended rollout for multiple replicas

1. Deploy a one-off migration job using the same image:
   ```bash
   docker run --rm \
     -e DATABASE_URL=... -e ENVIRONMENT=production -e SECRET_KEY=... \
     enterprise-api-backend:1.0.0 alembic upgrade head
   ```
2. Set `RUN_MIGRATIONS=false` on the application containers.
3. Roll out the new application version.

### Writing a migration

```bash
alembic revision --autogenerate -m "add task estimate"
# review the generated file before committing
alembic upgrade head
```

Auto-generation is a draft, not a guarantee. Always review:

- `server_default` values for new non-nullable columns;
- index and constraint names;
- `downgrade()` correctness (CI verifies it);
- data backfills, which belong in an explicit `op.execute()`.

### Safety rules

- **Expand/contract.** Add columns nullable, backfill, then enforce in a later
  revision. Never rename a column in a single step on a live system.
- **Avoid long locks.** Create indexes with `postgresql_concurrently=True` where the
  table is large; run them outside a transaction block.
- **Back up before migrating.** A verified backup is the only rollback that survives a
  bad data migration.
- **Test the downgrade in staging.** CI runs `downgrade base` → `upgrade head` against
  PostgreSQL on every push.

---

## Scaling the rate limiter

The bundled `SlidingWindowRateLimiter` is **in-process**. With `WEB_CONCURRENCY=4`,
each worker independently allows `AUTH_RATE_LIMIT_PER_MINUTE` requests, so the effective
limit is 4× the configured value. Replicas multiply it again.

Choose one of:

### Option A — Redis-backed limiter

Replace the in-memory deque with a Redis sorted set or a sliding-window counter:

```python
# Sketch: sorted-set sliding window
async def hit(redis, key: str, limit: int, window: int) -> tuple[bool, int]:
    now = time.time()
    pipe = redis.pipeline()
    pipe.zremrangebyscore(key, 0, now - window)
    pipe.zcard(key)
    pipe.expire(key, window)
    _, count, _ = await pipe.execute()
    if count >= limit:
        return False, window
    await redis.zadd(key, {f"{now}:{uuid4().hex}": now})
    return True, 0
```

Keep the `hit(key) -> (allowed, retry_after)` contract so middleware changes are
minimal, and fail **open** or **closed** deliberately if Redis is unreachable (most
public APIs fail open on cache loss and rely on the gateway for hard limits).

### Option B — Enforce at the edge

Keep the application limiter as a per-process safety net and apply authoritative
limits in nginx (`limit_req_zone`), Cloudflare, or your API gateway:

```nginx
limit_req_zone $binary_remote_addr zone=auth:10m rate=20r/m;
limit_req_zone $binary_remote_addr zone=pdf:10m  rate=10r/m;

location ~ ^/api/v1/auth/(register|login|token|refresh)$ {
    limit_req zone=auth burst=5 nodelay;
    proxy_pass http://enterprise_api;
}

location ~ /report\.pdf$ {
    limit_req zone=pdf burst=2 nodelay;
    proxy_pass http://enterprise_api;
}
```

Whichever you choose, document the effective limit. The in-process limiter is a
convenience for single-process deployments, not a distributed security control.

---

## Scaling the application

- **Stateless by design.** No local session store, no sticky sessions required. Scale
  horizontally behind any load balancer.
- **Workers.** Uvicorn workers are processes; `WEB_CONCURRENCY=2` per vCPU is a sane
  default for this I/O-bound workload. PDF rendering consumes a thread per request, so
  very high concurrency benefits from more workers rather than more threads.
- **Database pool budget.** Total connections ≈ `replicas × (DB_POOL_SIZE +
  DB_MAX_OVERFLOW)`. Keep that below PostgreSQL's `max_connections`, leaving headroom
  for migrations and administration. A pooler (PgBouncer) is worthwhile beyond a
  handful of replicas.
- **Timeouts.** Set proxy read timeouts of 30–60 s. Auth and CRUD requests should
  finish in milliseconds; a slow request usually means a saturated database.
- **Graceful shutdown.** Uvicorn handles `SIGTERM`, stops accepting connections, and
  finishes in-flight requests. Give orchestrators a termination grace period of at
  least 30 s.

---

## Backups and recovery

### What to back up

| Item | Why |
| --- | --- |
| PostgreSQL database | All application state |
| `SECRET_KEY` (in your secret store) | Losing it invalidates every session; losing it *and* the DB is a full outage |
| Deployment configuration | Reproducible recovery |
| Image tag / lockfile | Deterministic rebuild |

The database is the only stateful component. Uploads do not exist; PDFs are generated
on demand.

### Logical backup

```bash
pg_dump --format=custom --no-owner --no-privileges \
  --file="enterprise_api_$(date +%F_%H%M).dump" "$DATABASE_URL"
```

Restore into an empty database:

```bash
pg_restore --clean --if-exists --no-owner --no-privileges \
  --dbname="$DATABASE_URL" enterprise_api_2026-10-25_0200.dump
```

### Physical / point-in-time

Enable WAL archiving and take periodic base backups (`pg_basebackup` or your
provider's managed equivalent). Target:

- **RPO** ≤ 5 minutes with continuous WAL shipping.
- **RTO** ≤ 30 minutes for a single-region restore.

### Verify restores

An unverified backup is a hope. On a schedule:

1. Restore the latest dump into a scratch database.
2. Run `alembic current` — it must match the deployed revision.
3. Start the image against it and hit `/health/ready`.
4. Log in with a known account and download a PDF report.

### Retention

Keep at least 7 daily, 4 weekly, and 12 monthly restore points, encrypted at rest and
stored in a different failure domain from the primary database.

---

## Observability

### Logs

The app writes structured, single-line logs to stdout. Each request line includes the
request id, method, path, status, and duration:

```
2026-10-25T10:14:03Z INFO app.api.middleware: GET /api/v1/projects -> 200 (18.4 ms) [request_id=0f4b7c1a...]
```

Ship stdout to your log platform. Useful queries:

- All lines for a `request_id` from a user report.
- `status >= 500` grouped by path.
- `Rate limit exceeded` warnings grouped by client IP.
- `Refresh token reuse detected` — a genuine security signal, alert on it.

Never log tokens, passwords, or `Authorization` headers. The code does not, and custom
logging should not either.

### Metrics

The service does not ship a metrics endpoint by design (it would need authentication
and a scrape contract). Recommended additions:

| Signal | Source |
| --- | --- |
| Request rate, latency, error ratio by route | Proxy/ingress metrics |
| 5xx rate and p95 latency | Proxy/ingress |
| DB connections, slow queries, replication lag | PostgreSQL exporter |
| Rate-limit rejections | Proxy logs or `429` counts |
| PDF generation latency | Add a histogram around `render_report` |
| Token reuse detections | Log-based counter |

### Health probes

| Probe | Use | Behaviour |
| --- | --- | --- |
| `/health/live` | Restart decision | Always `200` while the process serves |
| `/health/ready` | Load-balancer membership | `200` when `SELECT 1` succeeds, `503` otherwise |

Do not point restart policies at `/health/ready`; a database blip should drain traffic,
not kill containers.

### Alerting suggestions

- `5xx` ratio > 1% for 5 minutes.
- `/health/ready` failing on more than one instance.
- p95 latency > 1 s on auth routes.
- `refresh_token_reuse` events (possible token theft).
- Spike in `401` from a single IP (credential stuffing).
- Disk usage > 80% on the database host.

---

## Kubernetes notes

A minimal, production-shaped deployment:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: enterprise-api
spec:
  replicas: 3
  selector:
    matchLabels: { app: enterprise-api }
  template:
    metadata:
      labels: { app: enterprise-api }
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        seccompProfile: { type: RuntimeDefault }
      containers:
        - name: api
          image: registry.example.com/enterprise-api-backend:1.0.0
          ports:
            - containerPort: 8000
          envFrom:
            - secretRef: { name: enterprise-api-secrets }
            - configMapRef: { name: enterprise-api-config }
          env:
            - name: RUN_MIGRATIONS
              value: "false"        # migrations run as a Job
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: ["ALL"] }
          volumeMounts:
            - { name: tmp, mountPath: /tmp }
          readinessProbe:
            httpGet: { path: /health/ready, port: 8000 }
            initialDelaySeconds: 10
            periodSeconds: 10
            failureThreshold: 3
          livenessProbe:
            httpGet: { path: /health/live, port: 8000 }
            initialDelaySeconds: 20
            periodSeconds: 20
            failureThreshold: 3
          resources:
            requests: { cpu: 250m, memory: 256Mi }
            limits:   { cpu: "1",  memory: 1Gi }
      volumes:
        - name: tmp
          emptyDir: {}
---
apiVersion: batch/v1
kind: Job
metadata:
  name: enterprise-api-migrate
spec:
  backoffLimit: 3
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: migrate
          image: registry.example.com/enterprise-api-backend:1.0.0
          command: ["alembic", "upgrade", "head"]
          envFrom:
            - secretRef: { name: enterprise-api-secrets }
```

Also add:

- A `HorizontalPodAutoscaler` on CPU and request rate.
- A `PodDisruptionBudget` (`minAvailable: 2`) for safe node drains.
- A `NetworkPolicy` allowing ingress only from the ingress controller and egress only
  to PostgreSQL and your log/metrics endpoints.
- A `ServiceMonitor` if you add a metrics endpoint.

---

## Production checklist

Before the first deploy:

- [ ] `SECRET_KEY` generated, ≥ 32 chars, stored in a secret manager.
- [ ] `ENVIRONMENT=production`.
- [ ] `AUTO_CREATE_TABLES=false`; migrations applied by a job.
- [ ] `DATABASE_URL` uses `postgresql+asyncpg` and a least-privilege database role.
- [ ] `TRUSTED_HOSTS` set to real hostnames.
- [ ] `CORS_ORIGINS` lists exact front-end origins.
- [ ] `DOCS_ENABLED` decided (usually `false` for private APIs).
- [ ] TLS terminated in front of the app; HTTP redirects to HTTPS.
- [ ] `--proxy-headers` enabled only behind a trusted proxy.
- [ ] Body-size limit configured at the proxy to match `MAX_REQUEST_BODY_BYTES`.
- [ ] Rate limiting strategy chosen: single process, Redis, or gateway.
- [ ] Backups scheduled **and** a restore rehearsed.
- [ ] Logs shipped off-host with retention.
- [ ] Alerts on 5xx, readiness, and token-reuse events.
- [ ] `RUN_SEED=false`.
- [ ] Health probes wired to the right endpoints.
- [ ] Termination grace period ≥ 30 s.
