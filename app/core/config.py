"""Application configuration loaded from environment variables / .env file."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

INSECURE_SECRET_KEY = "dev-insecure-secret-key-change-me"


class Settings(BaseSettings):
    """Runtime settings.

    Values are read from environment variables first, then from a local ``.env``
    file. Variable names are case-insensitive (``DATABASE_URL`` == ``database_url``).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application ------------------------------------------------------------
    app_name: str = "Enterprise API"
    app_version: str = "1.0.0"
    environment: Literal["development", "staging", "production", "testing"] = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    docs_enabled: bool = True

    # Security ---------------------------------------------------------------
    secret_key: str = INSECURE_SECRET_KEY
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_expire_minutes: int = Field(default=30, ge=1, le=1440)
    refresh_token_expire_days: int = Field(default=7, ge=1, le=90)
    bcrypt_rounds: int = Field(default=12, ge=4, le=16)

    # Database ---------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/enterprise_api"
    db_echo: bool = False
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=20, ge=0, le=200)
    auto_create_tables: bool = True

    # HTTP -------------------------------------------------------------------
    # ``NoDecode`` disables JSON decoding so the comma separated validators below
    # receive the raw environment value.
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:3000",
        "http://localhost:5173",
    ]
    trusted_hosts: Annotated[list[str], NoDecode] = ["*"]
    max_request_body_bytes: int = Field(default=1_048_576, ge=1024)  # 1 MiB

    # Pagination -------------------------------------------------------------
    default_page_size: int = Field(default=20, ge=1, le=100)
    max_page_size: int = Field(default=100, ge=1, le=500)

    # Rate limiting ----------------------------------------------------------
    rate_limit_enabled: bool = True
    auth_rate_limit_per_minute: int = Field(default=20, ge=1)
    pdf_rate_limit_per_minute: int = Field(default=10, ge=1)

    # Reports ----------------------------------------------------------------
    pdf_max_tasks: int = Field(default=500, ge=1, le=5000)

    @field_validator("cors_origins", "trusted_hosts", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("api_v1_prefix")
    @classmethod
    def _normalise_prefix(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("api_v1_prefix must start with '/'")
        return value.rstrip("/")

    @field_validator("cors_origins")
    @classmethod
    def _validate_cors_origins(cls, value: list[str]) -> list[str]:
        # Credentials are allowed, so a wildcard origin is not permitted by CORS
        # and would be silently rejected by browsers.
        if "*" in value:
            raise ValueError("cors_origins may not contain '*' when credentials are enabled")
        return value

    @model_validator(mode="after")
    def _validate_production_settings(self) -> Settings:
        if self.environment == "production":
            if self.secret_key == INSECURE_SECRET_KEY or len(self.secret_key) < 32:
                raise ValueError(
                    "SECRET_KEY must be set to a random value of at least 32 characters "
                    "when ENVIRONMENT=production"
                )
            if self.auto_create_tables:
                raise ValueError(
                    "AUTO_CREATE_TABLES must be false in production; use 'alembic upgrade head'"
                )
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def access_token_ttl_seconds(self) -> int:
        return self.access_token_expire_minutes * 60

    @property
    def refresh_token_ttl_seconds(self) -> int:
        return self.refresh_token_expire_days * 86_400


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
