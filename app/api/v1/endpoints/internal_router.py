"""
app/api/v1/endpoints/internal_router.py
─────────────────────────────────────────
Mounts the internal-only endpoints under /api/v1/internal/
"""
from fastapi import APIRouter
from app.api.v1.endpoints import internal

internal_router = APIRouter()
internal_router.include_router(
    internal.router,
    prefix="/internal",
    tags=["Internal"],
)
