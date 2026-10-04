"""
app/core/security.py
─────────────────────
Authentication and RBAC utilities for ushanr.

Design:
- Tokens are JWTs issued by ushauth (shared SECRET_KEY / JWT_SECRET_KEY).
- If local signature verification fails (e.g. the secrets drifted between
  environments), the token is verified by ushauth itself via /me/ and the
  result is cached briefly. A warning is logged so the drift can be fixed.
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

# Tokens verified remotely by ushauth: token_hash → (claims, timestamp)
_verified_cache: dict[str, tuple[dict[str, Any], float]] = {}
_VERIFIED_CACHE_TTL = 60.0  # seconds
_secret_mismatch_warned = False


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


def _extract_codenames(data: dict[str, Any]) -> list[str]:
    """Pull RBAC codenames out of an ushauth /employees/me/ response envelope."""
    profile: dict = data
    for key in ("data", "result"):
        if isinstance(data.get(key), dict):
            profile = data[key]
            break

    permissions_block = profile.get("permissions", {})
    if not isinstance(permissions_block, dict):
        return []
    raw = permissions_block.get("codenames")
    if not isinstance(raw, list):
        return []
    if permissions_block.get("is_superuser", False):
        return ["*"]
    return raw


async def _verify_with_ushauth(token: str, settings: Settings) -> dict[str, Any] | None:
    """
    Fallback verification: ask ushauth (the token issuer) whether the token is
    valid by calling its /me/ endpoint. Returns the token claims on success,
    None if ushauth rejects the token or is unreachable.
    """
    import time

    try:
        claims = jwt.get_unverified_claims(token)
    except JWTError:
        return None  # Not even a well-formed JWT

    exp = claims.get("exp")
    if isinstance(exp, (int, float)) and exp <= time.time():
        return None  # Expired — no need to bother ushauth

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    cached = _verified_cache.get(token_hash)
    if cached and time.monotonic() - cached[1] < _VERIFIED_CACHE_TTL:
        return cached[0]

    ushauth_url = getattr(settings, "USHAUTH_BASE_URL", "").rstrip("/")
    if not ushauth_url:
        return None

    is_employee = claims.get("user_type") in ("employee", "admin")
    path = "/api/v1/employees/me/" if is_employee else "/api/v1/customers/me/"
    ushspa_token = getattr(settings, "USHSPA_TOKEN", "")
    headers = {
        "Authorization": f"Bearer {token}",
        "X-USHSPA-TOKEN": ushspa_token,
        "USHSPA-TOKEN": ushspa_token,
        "Accept": "application/json",
        "X-Forwarded-Proto": "https",
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{ushauth_url}{path}", headers=headers)
    except Exception as exc:
        logger.warning("ushanr_ushauth_token_verify_failed", error=str(exc))
        return None

    # 404 = valid token but no employee/customer profile; ushauth still
    # authenticated the bearer, so identity is confirmed.
    if response.status_code not in (200, 404):
        return None

    global _secret_mismatch_warned
    if not _secret_mismatch_warned:
        _secret_mismatch_warned = True
        logger.warning(
            "ushanr_jwt_secret_mismatch",
            message=(
                "Token rejected by local SECRET_KEY but accepted by ushauth. "
                "Set ushanr JWT_SECRET_KEY/SECRET_KEY to ushauth's JWT signing key."
            ),
        )

    now = time.monotonic()
    _verified_cache[token_hash] = (claims, now)

    # Warm the permission cache from the same response (saves a second call)
    if is_employee and response.status_code == 200:
        try:
            _perm_cache[token_hash] = (_extract_codenames(response.json()), now)
        except Exception:
            pass

    return claims


async def authenticate_token(token: str, settings: Settings | None = None) -> dict[str, Any]:
    """
    Verify a bearer token and return its claims.

    1. Fast path: local HS256 verification with the shared secret.
    2. Fallback: verification by ushauth (handles secret drift between services).
    """
    try:
        return decode_token(token)
    except HTTPException:
        claims = await _verify_with_ushauth(token, settings or get_settings())
        if claims is None:
            raise
        return claims


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, Any]:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return await authenticate_token(credentials.credentials)


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, Any] | None:
    if credentials is None:
        return None
    try:
        return await authenticate_token(credentials.credentials)
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

    codenames = _extract_codenames(data)

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

        claims = await authenticate_token(credentials.credentials, settings)

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
    claims = await authenticate_token(credentials.credentials)
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
