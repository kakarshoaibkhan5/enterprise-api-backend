"""Focused unit tests for the security, config, limiter, and schema primitives."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone

import jwt
import pytest
from pydantic import ValidationError

from app.core.config import INSECURE_SECRET_KEY, Settings, get_settings
from app.core.exceptions import (
    AppError,
    AuthenticationError,
    InvalidCredentialsError,
    PayloadTooLargeError,
    RateLimitError,
)
from app.core.logging import request_id_ctx
from app.core.pagination import Page, PageParams
from app.core.ratelimit import SlidingWindowRateLimiter
from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.core.timeutils import as_utc, utcnow


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
def test_password_hashing_round_trip() -> None:
    hashed = hash_password("Str0ngPassw0rd!")

    assert hashed != "Str0ngPassw0rd!"
    assert hashed.startswith("$2")
    assert verify_password("Str0ngPassw0rd!", hashed) is True
    assert verify_password("wrong-password", hashed) is False
    # Two hashes of the same password must differ (random salt).
    assert hash_password("Str0ngPassw0rd!") != hashed


def test_verify_password_never_raises_on_malformed_hashes() -> None:
    assert verify_password("anything", "not-a-bcrypt-hash") is False
    assert verify_password("anything", "") is False


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
def test_access_token_round_trip() -> None:
    token = create_access_token("user-123", token_version=4)
    payload = decode_token(token, expected_type="access")

    assert payload["sub"] == "user-123"
    assert payload["type"] == "access"
    assert payload["ver"] == 4
    assert isinstance(payload["jti"], str) and payload["jti"]
    assert payload["exp"] > payload["iat"]


def test_refresh_token_round_trip_and_metadata() -> None:
    before = utcnow()
    token, jti, expires_at = create_refresh_token("user-456", token_version=0)
    payload = decode_token(token, expected_type="refresh")

    assert payload["sub"] == "user-456"
    assert payload["type"] == "refresh"
    assert payload["jti"] == jti
    assert expires_at > before
    assert expires_at.tzinfo is not None


def test_token_type_is_enforced() -> None:
    refresh_token, _, _ = create_refresh_token("user-1")
    access_token = create_access_token("user-1")

    with pytest.raises(TokenError, match="Expected token type 'access'"):
        decode_token(refresh_token, expected_type="access")
    with pytest.raises(TokenError, match="Expected token type 'refresh'"):
        decode_token(access_token, expected_type="refresh")


def test_expired_tokens_are_rejected() -> None:
    settings = get_settings()
    now = utcnow()
    expired = jwt.encode(
        {
            "sub": "user-1",
            "type": "access",
            "jti": "abc",
            "ver": 0,
            "iat": now - timedelta(minutes=10),
            "exp": now - timedelta(seconds=1),
        },
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(TokenError, match="Token has expired"):
        decode_token(expired, expected_type="access")


def test_tokens_signed_with_another_key_are_rejected() -> None:
    settings = get_settings()
    forged = jwt.encode(
        {
            "sub": "user-1",
            "type": "access",
            "jti": "abc",
            "ver": 0,
            "iat": utcnow(),
            "exp": utcnow() + timedelta(minutes=5),
        },
        "a-different-secret-key-that-is-long-enough",
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(TokenError, match="Token is invalid"):
        decode_token(forged, expected_type="access")


def test_malformed_and_tampered_tokens_are_rejected() -> None:
    valid = create_access_token("user-1")

    for candidate in ("not-a-token", "", valid + "x", "a.b.c"):
        with pytest.raises(TokenError, match="Token is invalid"):
            decode_token(candidate, expected_type="access")


def test_tokens_missing_required_claims_are_rejected() -> None:
    settings = get_settings()
    incomplete = jwt.encode(
        {"sub": "user-1", "type": "access", "jti": "abc", "iat": utcnow()},
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(TokenError, match="Token is invalid"):
        decode_token(incomplete, expected_type="access")


def test_non_integer_token_version_is_rejected() -> None:
    settings = get_settings()
    bad_version = jwt.encode(
        {
            "sub": "user-1",
            "type": "access",
            "jti": "abc",
            "ver": "zero",
            "iat": utcnow(),
            "exp": utcnow() + timedelta(minutes=5),
        },
        settings.secret_key,
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(TokenError, match="Token version is invalid"):
        decode_token(bad_version, expected_type="access")


def test_hash_token_is_deterministic_and_one_way() -> None:
    token = "a-refresh-token"
    digest = hash_token(token)

    assert len(digest) == 64
    assert digest == hash_token(token)
    assert digest != hash_token("another-token")
    assert token not in digest


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
async def test_sliding_window_enforces_the_limit() -> None:
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60)

    assert await limiter.hit("client:a") == (True, 0)
    assert await limiter.hit("client:a") == (True, 0)

    allowed, retry_after = await limiter.hit("client:a")
    assert allowed is False
    assert 1 <= retry_after <= 61

    # Separate keys have independent budgets.
    assert (await limiter.hit("client:b"))[0] is True


async def test_sliding_window_releases_after_the_window() -> None:
    limiter = SlidingWindowRateLimiter(limit=1, window_seconds=0.05)

    assert (await limiter.hit("key"))[0] is True
    assert (await limiter.hit("key"))[0] is False

    await asyncio.sleep(0.08)

    assert (await limiter.hit("key"))[0] is True


async def test_rate_limiter_reset_clears_all_keys() -> None:
    limiter = SlidingWindowRateLimiter(limit=1, window_seconds=60)
    await limiter.hit("a")
    await limiter.hit("b")
    assert (await limiter.hit("a"))[0] is False

    await limiter.reset()

    assert (await limiter.hit("a"))[0] is True


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------
def test_page_params_compute_offset_and_limit() -> None:
    params = PageParams(page=3, per_page=10)

    assert params.page == 3
    assert params.per_page == 10
    assert params.offset == 20
    assert params.limit == 10


def test_page_metadata_for_intermediate_page() -> None:
    params = PageParams(page=2, per_page=2)
    page = Page.create(["c", "d"], total=5, params=params)

    assert page.items == ["c", "d"]
    assert page.total == 5
    assert page.pages == 3
    assert page.has_next is True
    assert page.has_prev is True


def test_page_metadata_for_last_and_empty_pages() -> None:
    last = Page.create(["e"], total=5, params=PageParams(page=3, per_page=2))
    assert last.has_next is False
    assert last.has_prev is True

    empty = Page.create([], total=0, params=PageParams(page=1, per_page=20))
    assert empty.items == []
    assert empty.pages == 0
    assert empty.has_next is False
    assert empty.has_prev is False


def test_page_serialises_the_documented_envelope() -> None:
    page = Page.create([1, 2], total=2, params=PageParams(page=1, per_page=20))
    body = page.model_dump()

    assert set(body) == {
        "items",
        "total",
        "page",
        "per_page",
        "pages",
        "has_next",
        "has_prev",
    }


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------
def test_utcnow_is_timezone_aware_utc() -> None:
    now = utcnow()
    assert now.tzinfo is UTC
    assert abs((datetime.now(UTC) - now).total_seconds()) < 5


def test_as_utc_attaches_utc_to_naive_datetimes() -> None:
    naive = datetime(2026, 1, 2, 3, 4, 5)
    normalised = as_utc(naive)

    assert normalised.tzinfo is UTC
    assert normalised.replace(tzinfo=None) == naive


def test_as_utc_converts_aware_datetimes_to_utc() -> None:
    plus_two = timezone(timedelta(hours=2))
    local = datetime(2026, 1, 2, 12, 0, tzinfo=plus_two)

    normalised = as_utc(local)

    assert normalised.tzinfo is UTC
    assert normalised.hour == 10


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def test_config_splits_comma_separated_lists() -> None:
    settings = Settings(
        _env_file=None,
        cors_origins="http://a.example, http://b.example",
        trusted_hosts="api.example, *.example",
    )

    assert settings.cors_origins == ["http://a.example", "http://b.example"]
    assert settings.trusted_hosts == ["api.example", "*.example"]


def test_config_normalises_the_api_prefix() -> None:
    assert Settings(_env_file=None, api_v1_prefix="/api/v2/").api_v1_prefix == "/api/v2"

    with pytest.raises(ValidationError, match="must start with '/'"):
        Settings(_env_file=None, api_v1_prefix="api/v2")


def test_config_rejects_wildcard_cors_with_credentials() -> None:
    with pytest.raises(ValidationError, match="may not contain"):
        Settings(_env_file=None, cors_origins="*")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"environment": "production", "secret_key": INSECURE_SECRET_KEY}, "SECRET_KEY"),
        ({"environment": "production", "secret_key": "too-short"}, "SECRET_KEY"),
        (
            {
                "environment": "production",
                "secret_key": "x" * 48,
                "auto_create_tables": True,
            },
            "AUTO_CREATE_TABLES",
        ),
    ],
)
def test_config_rejects_unsafe_production_settings(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(_env_file=None, **{"auto_create_tables": False, **overrides})


def test_config_accepts_safe_production_settings() -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        secret_key="s" * 48,
        auto_create_tables=False,
    )

    assert settings.is_production is True
    assert settings.access_token_ttl_seconds == 30 * 60
    assert settings.refresh_token_ttl_seconds == 7 * 86_400


def test_config_development_tolerates_defaults() -> None:
    settings = Settings(
        _env_file=None,
        environment="development",
        secret_key=INSECURE_SECRET_KEY,
        auto_create_tables=True,
    )

    assert settings.is_production is False
    assert settings.secret_key == INSECURE_SECRET_KEY
    assert settings.auto_create_tables is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"bcrypt_rounds": 3},
        {"access_token_expire_minutes": 0},
        {"default_page_size": 0},
        {"max_page_size": 501},
        {"max_request_body_bytes": 512},
        {"pdf_max_tasks": 5001},
    ],
)
def test_config_enforces_numeric_bounds(overrides: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)


def test_settings_properties_scale_with_configured_values() -> None:
    settings = Settings(
        _env_file=None,
        access_token_expire_minutes=5,
        refresh_token_expire_days=2,
    )

    assert settings.access_token_ttl_seconds == 300
    assert settings.refresh_token_ttl_seconds == 172_800


# ---------------------------------------------------------------------------
# Exception envelope primitives
# ---------------------------------------------------------------------------
def test_authentication_errors_inherit_the_bearer_challenge() -> None:
    error = AuthenticationError()
    assert error.status_code == 401
    assert error.code == "authentication_required"
    assert error.headers == {"WWW-Authenticate": "Bearer"}

    invalid = InvalidCredentialsError()
    assert invalid.code == "invalid_credentials"
    assert invalid.headers == {"WWW-Authenticate": "Bearer"}


def test_app_error_allows_overriding_class_defaults() -> None:
    error = AppError(
        "Custom failure",
        code="custom_error",
        status_code=418,
        details=[{"field": "name", "message": "bad"}],
        headers={},
    )

    assert error.message == "Custom failure"
    assert error.code == "custom_error"
    assert error.status_code == 418
    assert error.details == [{"field": "name", "message": "bad"}]
    assert error.headers == {}


def test_rate_limit_error_carries_retry_metadata() -> None:
    error = RateLimitError(retry_after=45)

    assert error.status_code == 429
    assert error.headers == {"Retry-After": "45"}
    assert error.details == [{"retry_after": 45}]


def test_payload_too_large_uses_the_modern_status_constant() -> None:
    error = PayloadTooLargeError()
    assert error.status_code == 413
    assert error.code == "payload_too_large"


def test_error_response_uses_the_active_request_id() -> None:
    from app.api.errors import error_response

    token = request_id_ctx.set("unit-request-id")
    try:
        response = error_response(
            status_code=422,
            code="validation_error",
            message="Invalid",
            details=[{"field": "name", "message": "required", "type": "missing"}],
        )
    finally:
        request_id_ctx.reset(token)

    assert response.status_code == 422
    body = response.body.decode()
    assert '"request_id":"unit-request-id"' in body
    assert '"field":"name"' in body
