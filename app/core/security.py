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


def _request_app_token(request: Request | None) -> str:
    """Application token (USHSPA_TOKEN) the *caller* sent to ushanr, if any."""
    if request is None:
        return ""
    for name in ("x-ushspa-token", "ushspa-token"):
        value = request.headers.get(name, "").strip().strip("\"'")
        if value:
            return value
    return ""


async def _ushauth_get(
    path: str,
    token: str,
    settings: Settings,
    caller_app_token: str = "",
) -> httpx.Response | None:
    """
    GET ``path`` on ushauth, trying every configured base URL in order.

    A URL is skipped (and the next tried) on connection errors, 5xx, redirects and
    400 (Django DisallowedHost / SSL-redirect style misconfiguration). Any other
    status (200, 401, 403, 404, ...) is a real answer from ushauth and is returned.

    Application token: ushanr's configured USHSPA_TOKEN is tried first. If ushauth
    answers 401 UNAUTHORIZED_APPLICATION (token missing/different in this
    environment) the app token the browser/proxy sent to ushanr is tried instead -
    it is the same pre-shared secret ushauth already accepted at login.

    Returns None when no URL produced an answer.
    """
    configured = getattr(settings, "USHSPA_TOKEN", "").strip().strip("\"'")
    app_tokens = [x for x in dict.fromkeys([configured, caller_app_token]) if x] or [""]
    if not configured:
        logger.warning("ushanr_ushspa_token_missing", message="USHSPA_TOKEN is empty; using caller's app token if present")

    urls = getattr(settings, "ushauth_urls", None) or []
    if not urls:
        logger.error("ushanr_ushauth_not_configured", message="USHAUTH_BASE_URL is empty")
        return None

    for base in urls:
        url = f"{base}{path}"
        last: httpx.Response | None = None
        failed = False
        for app_token in app_tokens:
            headers = {
                "Authorization": f"Bearer {token}",
                "X-USHSPA-TOKEN": app_token,
                "USHSPA-TOKEN": app_token,
                "Accept": "application/json",
                "X-Forwarded-Proto": "https",
            }
            try:
                async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                    response = await client.get(url, headers=headers)
            except Exception as exc:
                logger.error("ushanr_ushauth_unreachable", url=url, error=repr(exc))
                failed = True
                break

            sc = response.status_code
            if sc >= 500 or sc == 400 or 300 <= sc < 400:
                logger.error("ushanr_ushauth_bad_response", url=url, status=sc, body=response.text[:200])
                failed = True
                break

            last = response
            if sc == 401 and "UNAUTHORIZED_APPLICATION" in response.text:
                logger.error(
                    "ushanr_ushauth_app_token_rejected",
                    url=url,
                    message="ushauth rejected USHSPA_TOKEN; set ushanr USHSPA_TOKEN to ushauth's value",
                )
                continue  # try the next app token
            break

        if failed:
            continue
        if last is not None:
            if last.status_code in (401, 403):
                logger.warning("ushanr_ushauth_rejected", url=url, status=last.status_code, body=last.text[:200])
            return last
    return None


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


async def _verify_with_ushauth(
    token: str, settings: Settings, caller_app_token: str = ""
) -> dict[str, Any] | None:
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

    is_employee = claims.get("user_type") in ("employee", "admin")
    path = "/api/v1/employees/me/" if is_employee else "/api/v1/customers/me/"
    response = await _ushauth_get(path, token, settings, caller_app_token)
    if response is None:
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


async def authenticate_token(
    token: str, settings: Settings | None = None, request: Request | None = None
) -> dict[str, Any]:
    """
    Verify a bearer token and return its claims.

    1. Fast path: local HS256 verification with the shared secret.
    2. Fallback: verification by ushauth (handles secret drift between services).
    """
    try:
        return decode_token(token)
    except HTTPException:
        claims = await _verify_with_ushauth(
            token, settings or get_settings(), _request_app_token(request)
        )
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


async def _fetch_codenames_from_ushauth(
    token: str, settings: Settings, caller_app_token: str = ""
) -> list[str] | None:
    """
    Fetch RBAC permission codenames for the bearer from ushauth's
    /api/v1/employees/me/ endpoint.

    Returns None if the user has no employee profile (or ushauth sent an unusable reply).
    Raises HTTP 503 if ushauth cannot be reached on any configured URL.
    Returns ["*"] for superusers.
    """
    import time

    token_hash = hashlib.sha256(token.encode()).hexdigest()

    # Check in-process cache
    if token_hash in _perm_cache:
        codenames, ts = _perm_cache[token_hash]
        if time.monotonic() - ts < _PERM_CACHE_TTL:
            return codenames

    response = await _ushauth_get("/api/v1/employees/me/", token, settings, caller_app_token)

    if response is None:
        # Could not get ANY answer from ushauth: this is an infrastructure/config
        # problem, not a permission problem — surface it as 503, not 403.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "AUTH_SERVICE_UNAVAILABLE",
                "message": "Unable to verify permissions with the auth service.",
            },
        )
    if response.status_code == 401 and "UNAUTHORIZED_APPLICATION" in response.text:
        # ushauth rejected the application token (USHSPA_TOKEN) - a configuration
        # problem, not a permission problem.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "AUTH_SERVICE_MISCONFIGURED",
                "message": "ushauth rejected ushanr's application token (USHSPA_TOKEN).",
            },
        )
    if response.status_code in (401, 403):
        return []
    if response.status_code == 404:
        return None  # Valid token but no employee profile
    if response.status_code != 200:
        logger.error("ushanr_ushauth_unexpected_status", status=response.status_code, body=response.text[:200])
        return None
    try:
        data = response.json()
    except Exception as exc:
        logger.error("ushanr_ushauth_invalid_json", error=repr(exc))
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
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
        settings: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )

        claims = await authenticate_token(credentials.credentials, settings, request)

        # Superuser bypass
        if claims.get("is_superuser"):
            return claims

        # Fetch permissions from ushauth
        codenames = await _fetch_codenames_from_ushauth(
            credentials.credentials, settings, _request_app_token(request)
        )

        if codenames is None:
            # Valid token, but ushauth has no employee profile for this user
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "PERMISSION_DENIED",
                    "message": f"Permission '{codename}' is required.",
                    "required": codename,
                    "reason": "no_employee_profile",
                },
            )

        if codenames == ["*"] or codename in codenames:
            return claims

        logger.warning(
            "ushanr_permission_denied",
            required=codename,
            user_id=claims.get("user_id") or claims.get("sub"),
            granted_count=len(codenames),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "PERMISSION_DENIED",
                "message": f"Permission '{codename}' is required.",
                "required": codename,
                "reason": "role_lacks_permission",
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
