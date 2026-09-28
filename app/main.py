"""
app/main.py
────────────
FastAPI application factory with lifespan management.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.database import Base, dispose_engine, get_engine, get_session_factory
from app.core.exceptions import ANRBaseError
from app.core.logging import configure_logging, get_logger

# Import all models so Alembic and SQLAlchemy can discover them
import app.models  # noqa: F401

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context manager."""
    settings = get_settings()

    # ── Startup ───────────────────────────────────────────────────────────
    configure_logging(
        log_level=settings.LOG_LEVEL,
        is_development=settings.is_development,
    )

    logger.info(
        "service_starting",
        name=settings.APP_NAME,
        version=settings.APP_VERSION,
        env=settings.APP_ENV,
    )

    # Initialise DB engine and session factory
    try:
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("database_schema_initialized")
    except Exception as exc:
        logger.warning("database_schema_init_warning", error=str(exc))

    get_session_factory()

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────
    logger.info("service_shutting_down")
    await dispose_engine()
    logger.info("service_stopped")


def create_application() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="USHSPA Accounting & Reporting Service (ushanr)",
        description=(
            "Production-ready double-entry accounting microservice for SME accounting, "
            "reporting, invoicing, and financial management."
        ),
        version=settings.APP_VERSION,
        docs_url="/api/docs/" if not settings.is_production else None,
        redoc_url="/api/redoc/" if not settings.is_production else None,
        openapi_url="/api/schema/" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Exception handlers ─────────────────────────────────────────────────
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "error": {
                    "code": getattr(exc, "code", "HTTP_ERROR"),
                    "message": exc.detail if isinstance(exc.detail, str) else "HTTP Exception",
                    "detail": exc.detail if not isinstance(exc.detail, str) else None,
                },
            },
            headers=exc.headers,
        )

    @app.exception_handler(ANRBaseError)
    async def domain_exception_handler(request: Request, exc: ANRBaseError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "detail": exc.detail if settings.is_development else None,
                },
            },
        )

    def _sanitize_for_json(obj: Any) -> Any:
        if isinstance(obj, Exception):
            return str(obj)
        if isinstance(obj, dict):
            return {k: _sanitize_for_json(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [_sanitize_for_json(v) for v in obj]
        return obj

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Request validation failed.",
                    "detail": jsonable_encoder(_sanitize_for_json(exc.errors())),
                },
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "unhandled_exception",
            exc_type=type(exc).__name__,
            exc_str=str(exc),
            path=request.url.path,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "An unexpected error occurred.",
                },
            },
        )

    # ── Routes ─────────────────────────────────────────────────────────────
    app.include_router(api_router)
    if settings.USHANR_BASE_PATH:
        app.include_router(api_router, prefix=settings.USHANR_BASE_PATH)
    if settings.USHANR_BASE_PATH != "/anr":
        app.include_router(api_router, prefix="/anr")

    return app


app = create_application()
