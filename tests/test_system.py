"""Cross-cutting HTTP behaviour: health, docs, envelopes, headers, limits, CORS."""

from __future__ import annotations

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import middleware as middleware_module
from app.api.middleware import RateLimitMiddleware, RequestContextMiddleware
from app.api.v1 import health as health_module
from tests.helpers import error_code, register


async def test_liveness_and_root(client: AsyncClient) -> None:
    live = await client.get("/health/live")
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}

    root = await client.get("/")
    assert root.status_code == 200
    body = root.json()
    assert body["name"] == "Enterprise API"
    assert body["version"] == "1.0.0"
    assert body["environment"] == "testing"
    assert body["docs"] == "/docs"
    assert body["openapi"] == "/openapi.json"
    assert body["health"] == "/health/ready"
    assert body["api"] == "/api/v1"


async def test_readiness_checks_the_database(client: AsyncClient) -> None:
    ready = await client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ok",
        "version": "1.0.0",
        "environment": "testing",
        "database": "up",
    }

    alias = await client.get("/health")
    assert alias.status_code == 200
    assert alias.json()["database"] == "up"


async def test_readiness_degrades_when_the_database_is_unreachable(
    client: AsyncClient, monkeypatch
) -> None:
    async def unavailable() -> None:
        raise RuntimeError("database offline")

    monkeypatch.setattr(health_module, "ping_database", unavailable)

    for path in ("/health/ready", "/health"):
        response = await client.get(path)
        assert response.status_code == 503, path
        assert response.json()["status"] == "degraded"
        assert response.json()["database"] == "down"

    # Liveness must not depend on the database, or a blip would restart pods.
    assert (await client.get("/health/live")).status_code == 200


async def test_openapi_and_interactive_docs(client: AsyncClient) -> None:
    schema = await client.get("/openapi.json")
    assert schema.status_code == 200
    document = schema.json()
    assert document["info"]["title"] == "Enterprise API"
    paths = document["paths"]
    for expected in (
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/projects",
        "/api/v1/projects/{project_id}/tasks",
        "/api/v1/projects/{project_id}/report.pdf",
        "/api/v1/admin/users",
        "/health/ready",
    ):
        assert expected in paths, expected

    # Swagger UI advertises the same password flow the token endpoint serves.
    oauth = document["components"]["securitySchemes"]["OAuth2PasswordBearer"]
    assert oauth["type"] == "oauth2"
    assert oauth["flows"]["password"]["tokenUrl"] == "/api/v1/auth/token"

    for path in ("/docs", "/redoc"):
        response = await client.get(path)
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Content-Security-Policy" in response.headers


async def test_security_headers_on_every_response(client: AsyncClient) -> None:
    for path in ("/health/live", "/openapi.json"):
        response = await client.get(path)
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "geolocation=()" in response.headers["permissions-policy"]
        assert response.headers["cross-origin-opener-policy"] == "same-origin"

    # HSTS is only advertised by production deployments (TLS terminates in front).
    assert "strict-transport-security" not in (await client.get("/health/live")).headers

    # Error responses are hardened too.
    error = await client.get("/api/v1/nope")
    assert error.status_code == 404
    assert error.headers["x-content-type-options"] == "nosniff"


async def test_hsts_is_emitted_in_production(client: AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr(middleware_module.settings, "environment", "production")

    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.headers["strict-transport-security"] == ("max-age=31536000; includeSubDomains")

    # The docs CSP is applied independently of the environment.
    docs = await client.get("/docs")
    assert "default-src 'self'" in docs.headers["content-security-policy"]


async def test_unknown_routes_and_methods_use_the_error_envelope(
    client: AsyncClient,
) -> None:
    missing = await client.get("/api/v1/does-not-exist")
    assert missing.status_code == 404
    assert error_code(missing) == "not_found"
    assert missing.json()["error"]["message"] == "Not Found"

    wrong_method = await client.delete("/health/live")
    assert wrong_method.status_code == 405
    assert error_code(wrong_method) == "method_not_allowed"
    assert wrong_method.json()["error"]["message"] == "Method Not Allowed"

    # FastAPI's automatic 401 (no credentials) keeps the Bearer challenge.
    unauthenticated = await client.get("/api/v1/users/me")
    assert unauthenticated.status_code == 401
    assert error_code(unauthenticated) == "authentication_required"
    assert unauthenticated.headers["www-authenticate"] == "Bearer"


async def test_request_id_is_echoed_generated_and_used_in_errors(
    client: AsyncClient,
) -> None:
    supplied = await client.get("/health/live", headers={"X-Request-ID": "trace-123"})
    assert supplied.headers["x-request-id"] == "trace-123"

    generated = await client.get("/health/live")
    generated_id = generated.headers["x-request-id"]
    assert len(generated_id) == 32
    assert int(generated_id, 16) >= 0

    failure = await client.get("/api/v1/nope", headers={"X-Request-ID": "trace-abc"})
    assert failure.status_code == 404
    assert failure.headers["x-request-id"] == "trace-abc"
    assert failure.json()["error"]["request_id"] == "trace-abc"

    # Oversized identifiers are truncated so they cannot flood logs.
    truncated = await client.get("/health/live", headers={"X-Request-ID": "z" * 300})
    assert truncated.headers["x-request-id"] == "z" * 128


async def test_oversized_request_bodies_are_rejected_early(client: AsyncClient) -> None:
    payload = await register(client)
    body = {"name": "x" * 1_100_000}

    response = await client.post(
        "/api/v1/projects",
        headers={"Authorization": f"Bearer {payload['tokens']['access_token']}"},
        json=body,
    )
    assert response.status_code == 413
    assert error_code(response) == "payload_too_large"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


async def test_cors_allows_configured_origin_only(client: AsyncClient) -> None:
    preflight = await client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,authorization",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert preflight.headers["access-control-allow-credentials"] == "true"
    allowed = preflight.headers["access-control-allow-methods"]
    assert "POST" in allowed
    assert preflight.headers["access-control-allow-headers"].lower().find("authorization") >= 0

    # Exposed response headers apply to actual requests, not the preflight.
    simple = await client.get("/health/live", headers={"Origin": "http://localhost:3000"})
    exposed = simple.headers["access-control-expose-headers"]
    assert "X-Request-ID" in exposed
    assert "Content-Disposition" in exposed

    foreign = await client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert foreign.headers.get("access-control-allow-origin") != "https://evil.example.com"


async def test_unhandled_exceptions_return_a_traceable_envelope() -> None:
    isolated = FastAPI()

    # RequestContext must be inside SecurityHeaders so that the fallback 500 it
    # synthesises still receives the hardening headers.
    isolated.add_middleware(middleware_module.RequestContextMiddleware)
    isolated.add_middleware(middleware_module.SecurityHeadersMiddleware)

    @isolated.get("/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    transport = ASGITransport(app=isolated, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        response = await http_client.get("/boom", headers={"X-Request-ID": "unit-boom"})

    assert response.status_code == 500
    assert response.headers["x-request-id"] == "unit-boom"
    assert response.headers["x-content-type-options"] == "nosniff"
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert body["error"]["request_id"] == "unit-boom"
    assert body["error"]["details"] == []
    # The internal message must never leak exception text.
    assert "kaboom" not in body["error"]["message"]


async def test_rate_limit_middleware_returns_429_with_retry_after(monkeypatch) -> None:
    monkeypatch.setattr(middleware_module.settings, "rate_limit_enabled", True)
    monkeypatch.setattr(middleware_module.settings, "auth_rate_limit_per_minute", 2)

    isolated = FastAPI()
    isolated.add_middleware(RateLimitMiddleware)
    isolated.add_middleware(RequestContextMiddleware)

    @isolated.post("/api/v1/auth/login")
    async def login() -> dict[str, bool]:
        return {"ok": True}

    transport = ASGITransport(app=isolated)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        for _ in range(2):
            allowed = await http_client.post("/api/v1/auth/login")
            assert allowed.status_code == 200
            assert allowed.headers["x-ratelimit-limit"] == "2"

        limited = await http_client.post("/api/v1/auth/login")
        assert limited.status_code == 429
        assert limited.headers["x-ratelimit-limit"] == "2"
        assert int(limited.headers["retry-after"]) >= 1
        assert limited.json()["error"]["code"] == "rate_limit_exceeded"
        assert limited.json()["error"]["request_id"] == limited.headers["x-request-id"]

        # Unrelated routes are not throttled by the auth budget.
        assert (await http_client.get("/unrelated")).status_code == 404


async def test_request_context_middleware_ignores_non_http_scopes() -> None:
    """Lifespan scopes must pass straight through to the wrapped application."""
    calls: list[str] = []

    async def inner(scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        calls.append(scope["type"])

    await RequestContextMiddleware(inner)(
        {"type": "lifespan"},
        None,
        None,  # type: ignore[arg-type]
    )
    assert calls == ["lifespan"]


def test_request_id_context_starts_blank() -> None:
    from app.core.logging import request_id_ctx

    assert request_id_ctx.get() == "-"
