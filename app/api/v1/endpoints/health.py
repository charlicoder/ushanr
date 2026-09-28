"""
app/api/v1/endpoints/health.py
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/", summary="Health check")
async def health_check() -> dict:
    return {"status": "ok", "service": "ushanr"}
