"""
app/core/config.py
──────────────────
Settings management using pydantic-settings.
All configuration is sourced from environment variables / .env file.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ── App ───────────────────────────────────────────────────────────────
    APP_NAME: str = "ushanr"
    APP_VERSION: str = "1.0.0"
    APP_ENV: Literal["development", "staging", "production"] = "development"
    LOG_LEVEL: str = "INFO"
    SECRET_KEY: str = "change-me-in-production-please"

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
            url = "postgresql+asyncpg://postgres:postgres@localhost:5432/ushanr"

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
        description="Shared application token for inter-service requests.",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
