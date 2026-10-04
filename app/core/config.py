"""
app/core/config.py
──────────────────
Settings management using pydantic-settings.
All configuration is sourced from environment variables / .env file.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
    )

    # ── App ───────────────────────────────────────────────────────────────
    APP_NAME: str = "ushanr"
    APP_VERSION: str = "1.0.0"
    APP_ENV: Literal["development", "staging", "production"] = "development"
    LOG_LEVEL: str = "INFO"
    # HS256 key used to verify JWTs issued by ushauth. It MUST equal ushauth's
    # SIMPLE_JWT["SIGNING_KEY"] (JWT_SECRET_KEY, falling back to DJANGO_SECRET_KEY).
    # Read from the same env var names as ushauth so both services can share one
    # value. Precedence: JWT_SECRET_KEY > SECRET_KEY > DJANGO_SECRET_KEY.
    # The default matches ushauth's default so local dev works out of the box.
    SECRET_KEY: str = Field(
        default="change-me-in-production",
        validation_alias=AliasChoices("JWT_SECRET_KEY", "SECRET_KEY", "DJANGO_SECRET_KEY"),
    )

    # ── Server ────────────────────────────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 8007
    WORKERS: int = 2

    # ── Database ──────────────────────────────────────────────────────────
    DATABASE_URL: str | None = Field(
        default=None,
        description="Async PostgreSQL DSN",
    )
    DB_USER: str | None = None
    DB_PASSWORD: str | None = None
    DB_HOST: str | None = None
    DB_PORT: int | str = 5432
    DB_NAME: str | None = None
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 1800
    DB_ECHO: bool = False

    @property
    def async_database_url(self) -> str:
        if self.DATABASE_URL and self.DATABASE_URL.strip():
            url = self.DATABASE_URL.strip()
        elif self.DB_USER and self.DB_HOST and self.DB_NAME:
            pwd = f":{self.DB_PASSWORD}" if self.DB_PASSWORD else ""
            url = f"postgresql+asyncpg://{self.DB_USER}{pwd}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        else:
            url = "postgresql+asyncpg://postgres:postgres@host.docker.internal:5432/ushanr"

        import os
        is_container = os.path.exists("/.dockerenv") or bool(os.environ.get("IN_DOCKER"))

        if is_container:
            # Inside Docker container: localhost/127.0.0.1 refers to the container itself.
            # If host.docker.internal is available, route to the host machine.
            if "@localhost:" in url or "@127.0.0.1:" in url:
                try:
                    import socket
                    socket.gethostbyname("host.docker.internal")
                    url = url.replace("@localhost:", "@host.docker.internal:").replace("@127.0.0.1:", "@host.docker.internal:")
                except Exception:
                    pass
        else:
            # Outside Docker (local machine): host.docker.internal might not resolve.
            if "host.docker.internal" in url:
                try:
                    import socket
                    socket.gethostbyname("host.docker.internal")
                except Exception:
                    url = url.replace("host.docker.internal", "localhost")
        return url

    # ── Base paths ────────────────────────────────────────────────────────
    USHANR_BASE_PATH: str = "/uanr"

    # ── CORS ──────────────────────────────────────────────────────────────
    ALLOWED_ORIGINS: list[str] = ["*"]

    # ── Rate limiting ─────────────────────────────────────────────────────
    RATE_LIMIT_REQUESTS: int = 200
    RATE_LIMIT_WINDOW_SECONDS: int = 60

    # ── Pagination ────────────────────────────────────────────────────────
    DEFAULT_PAGE_SIZE: int = 25
    MAX_PAGE_SIZE: int = 200

    # ── Default accounting settings ───────────────────────────────────────
    DEFAULT_CURRENCY: str = "KWD"
    DEFAULT_DECIMAL_PLACES: int = 3

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    # ── Inter-service ─────────────────────────────────────────────────────
    USHAUTH_BASE_URL: str = Field(
        default="http://ushauth:8000",
        description="Base URL of the ushauth service for permission resolution.",
    )
    USHSPA_TOKEN: str = Field(
        default="",
        validation_alias=AliasChoices("USHSPA_TOKEN", "USH_TOKEN"),
        description="Shared application token for inter-service requests.",
    )
    # Optional API-gateway fallback used when USHAUTH_BASE_URL is unreachable or
    # misconfigured (same approach as ushbooknpay). Example:
    #   API_GATEWAY_BASE_URL=https://api.ushspa.co   USHAUTH_BASE_PATH=/uauth
    API_GATEWAY_BASE_URL: str = Field(default="")
    USHAUTH_BASE_PATH: str = Field(default="/uauth")
    @property
    def ushauth_urls(self) -> list[str]:
        """Candidate ushauth base URLs, in order of preference (deduplicated)."""
        urls: list[str] = []
        if self.USHAUTH_BASE_URL and self.USHAUTH_BASE_URL.strip():
            urls.append(self.USHAUTH_BASE_URL.strip().rstrip("/"))
        if self.API_GATEWAY_BASE_URL and self.API_GATEWAY_BASE_URL.strip():
            path = "/" + self.USHAUTH_BASE_PATH.strip().strip("/") if self.USHAUTH_BASE_PATH.strip("/ ") else ""
            urls.append(f"{self.API_GATEWAY_BASE_URL.strip().rstrip('/')}{path}")
        return list(dict.fromkeys(urls))

    INTERNAL_API_KEY: str = Field(
        default="ushanr-internal-secret-change-in-prod",
        description="Shared secret for internal service-to-service endpoints (X-Internal-Key header).",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
