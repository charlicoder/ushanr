"""
app/core/security.py
─────────────────────
Authentication and RBAC utilities for ushanr.

Design:
- Tokens are JWTs issued by ushauth (shared SECRET_KEY).
- For RBAC, the JWT payload carries user identity only (user_id, user_type, is_superuser).
- Permission codenames are fetched from ushauth's /api/v1/employees/me/ endpoint
  and cached in-process (or via Redis if configured) for 5 minutes.
- Superusers bypass all permission checks.
- Customers (user_type='customer') are denied from employee-only endpoints.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from functools import lru_cache

import httpx
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
bearer_scheme = HTTPBearer(auto_error=False)

# In-process permission cache: token_hash → (codenames, timestamp)
# A more production-grade solution would use Redis (same as ushbooknpay).
_perm_cache: dict[str, tuple[list[str], float]] = {}
_PERM_CACHE_TTL = 300.0  # 5 minutes


def decode_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=["HS256"],
            options={"verify_exp": True},
        )
        return payload
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, Any]:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return decode_token(credentials.credentials)


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, Any] | None:
    if credentials is None:
        return None
    try:
        return decode_token(credentials.credentials)
    except HTTPException:
        return None


async def _fetch_codenames_from_ushauth(token: str, settings: Settings) -> list[str] | None:
    """
    Fetch RBAC permission codenames for the bearer from ushauth's
    /api/v1/employees/me/ endpoint.

    Returns None if the service is unavailable or the user has no employee profile.
    Returns ["*"] for superusers.
    """
    import time

    token_hash = hashlib.sha256(token.encode()).hexdigest()

    # Check in-process cache
    if token_hash in _perm_cache:
        codenames, ts = _perm_cache[token_hash]
        if time.monotonic() - ts < _PERM_CACHE_TTL:
            return codenames

    ushauth_url = getattr(settings, "USHAUTH_BASE_URL", "").rstrip("/")
    ushspa_token = getattr(settings, "USHSPA_TOKEN", "")

    if not ushauth_url:
        logger.warning("USHAUTH_BASE_URL not configured; permission check skipped")
        return None

    url = f"{ushauth_url}/api/v1/employees/me/"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-USHSPA-TOKEN": ushspa_token,
        "USHSPA-TOKEN": ushspa_token,
        "Accept": "application/json",
        "X-Forwarded-Proto": "https",
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url, headers=headers)

        if response.status_code in (401, 403):
            return []
        if response.status_code == 404:
            return None  # Not an employee
        response.raise_for_status()
        data = response.json()

    except Exception as exc:
        logger.warning("ushanr_ushauth_permission_fetch_failed", error=str(exc))
        return None

    # Navigate the response envelope
    profile: dict = data
    for key in ("data", "result"):
        if isinstance(data.get(key), dict):
            profile = data[key]
            break

    permissions_block = profile.get("permissions", {})
    if isinstance(permissions_block, dict):
        raw = permissions_block.get("codenames")
        if isinstance(raw, list):
            codenames = raw
            is_superuser = permissions_block.get("is_superuser", False)
            if is_superuser:
                codenames = ["*"]
        else:
            codenames = []
    else:
        codenames = []

    # Cache result
    _perm_cache[token_hash] = (codenames, time.monotonic())
    return codenames


def require_permission(codename: str):
    """
    FastAPI dependency factory: verifies the authenticated user holds a specific
    RBAC permission codename.

    Uses the JWT for authentication, then fetches permission codenames from ushauth.
    Superusers (codenames=["*"] or is_superuser claim) bypass all checks.

    Usage:
        @router.get("/invoices/", dependencies=[Depends(require_permission("invoices.list"))])
        async def list_invoices(...): ...
    """

    async def _check(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
        settings: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )

        claims = decode_token(credentials.credentials)

        # Superuser bypass
        if claims.get("is_superuser"):
            return claims

        # Fetch permissions from ushauth
        codenames = await _fetch_codenames_from_ushauth(credentials.credentials, settings)

        if codenames is None:
            # ushauth unavailable or user is not an employee
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "PERMISSION_DENIED",
                    "message": f"Permission '{codename}' is required.",
                    "required": codename,
                },
            )

        if codenames == ["*"] or codename in codenames:
            return claims

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "PERMISSION_DENIED",
                "message": f"Permission '{codename}' is required.",
                "required": codename,
            },
        )

    return _check


async def require_employee_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, Any]:
    """
    FastAPI dependency: ensures the authenticated user is an employee or admin.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    claims = decode_token(credentials.credentials)
    user_type = claims.get("user_type", "customer")
    if user_type not in ("employee", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "EMPLOYEES_ONLY",
                "message": "This endpoint is restricted to employee accounts.",
            },
        )
    return claims
