"""API-key authentication dependencies for FastAPI routes."""

import asyncio
import hmac
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import Header, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.platform.config.snapshot import get_config

_security = HTTPBearer(auto_error=False, scheme_name="API Key")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_keys() -> list[str]:
    raw = get_config("app.api_key", "")
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(k).strip() for k in raw if str(k).strip()]
    return [k.strip() for k in str(raw).split(",") if k.strip()]


def get_admin_key() -> str:
    """Return configured ``app.app_key`` (admin password)."""
    return str(get_config("app.app_key", "grok2api") or "")


def get_webui_key() -> str:
    """Return configured ``app.webui_key`` (webui access key)."""
    return str(get_config("app.webui_key", "") or "")


def is_webui_enabled() -> bool:
    """Whether the webui entry is enabled."""
    val = get_config("app.webui_enabled", False)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in {"1", "true", "yes", "on"}
    return bool(val)


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


async def verify_api_key(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
):
    """Validate Bearer token against configured ``api_key`` and/or client keys.

    Accepts either ``Authorization: Bearer <key>`` (OpenAI / grok2api style)
    or ``X-API-Key: <key>`` (official Anthropic SDK style) so that agents
    targeting the Anthropic-compatible endpoint work without reconfiguration.

    Authentication order (client keys are a strict addition, never a
    weakening of the configured ``app.api_key``):
      1. Token matches ``app.api_key`` (any of the list) → pass.
      2. Token has the ``grok2api_`` client-key shape and the client key
         store is initialised → must authenticate against it (enabled,
         not expired, within rpm/max_concurrent limits); a malformed or
         unknown client key is rejected even in open mode so issued keys
         cannot be shadowed.
      3. Otherwise open mode (no ``app.api_key`` configured) → pass;
         non-empty ``app.api_key`` → 403.

    On client-key auth, ``request.state.client_key_id`` /
    ``client_key_name`` are set for audit recording.  The generator form
    releases the per-key concurrency slot when the request finishes.
    """
    allowed_keys = _get_keys()

    token = _extract_bearer(authorization) or x_api_key or None
    if token is None:
        if not allowed_keys:
            request.state.auth_kind = "open"
            yield
            return
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Missing or invalid Authorization header."
        )

    if allowed_keys and any(hmac.compare_digest(token, k) for k in allowed_keys):
        request.state.auth_kind = "app_api_key"
        yield
        return

    # Client-key path: key-shaped tokens authenticate strictly against the
    # client key store and never fall through to open mode — an unknown,
    # disabled or expired issued key is rejected even when no app.api_key is
    # configured, so issued keys cannot be shadowed.
    client_key = None
    if token.startswith("grok2api_"):
        repo = getattr(request.app.state, "client_keys", None)
        if repo is not None:
            client_key = await _authenticate_client_key(request, repo, token)
            if client_key is None:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid API key.")

    if client_key is None:
        if not allowed_keys:
            request.state.auth_kind = "open"
            yield
            return
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid API key.")

    request.state.auth_kind = "client_key"
    request.state.client_key_id = client_key.id
    request.state.client_key_name = client_key.name
    request.state.client_key = client_key
    try:
        yield
    finally:
        _release_concurrency(client_key.id)


# ---------------------------------------------------------------------------
# Client-key authentication + in-memory rate limiting
# ---------------------------------------------------------------------------
# ponytail: per-process in-memory counters; the default single-worker Granian
# deployment is exact.  Multi-worker deployments would need a shared store
# (Redis) — add when workers > 1.

import asyncio
import time
from collections import defaultdict, deque

_KEY_PREFIX = "grok2api_"

_rpm_windows: dict[int, deque[float]] = defaultdict(deque)
_active: dict[int, int] = defaultdict(int)
_rate_lock = asyncio.Lock()
_RPM_WINDOW_SECONDS = 60.0


async def _authenticate_client_key(request: Request, repo, token: str) -> Any | None:
    prefix = token[: len(_KEY_PREFIX) + 8]
    key = await repo.get_by_prefix(prefix)
    if key is None:
        return None
    if not hmac.compare_digest(token, key.secret):
        return None
    if not key.is_available(int(time.time() * 1000)):
        return None

    now = time.monotonic()
    async with _rate_lock:
        window = _rpm_windows[key.id]
        while window and now - window[0] > _RPM_WINDOW_SECONDS:
            window.popleft()
        if key.rpm_limit > 0 and len(window) >= key.rpm_limit:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS, "Client key rate limit exceeded."
            )
        if key.max_concurrent > 0 and _active[key.id] >= key.max_concurrent:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Client key concurrency limit exceeded.",
            )
        window.append(now)
        _active[key.id] += 1
    return key


def _release_concurrency(key_id: int) -> None:
    _active[key_id] = max(0, _active[key_id] - 1)


async def verify_admin_key(
    authorization: str | None = Header(default=None),
    app_key: str | None = Query(default=None),
) -> None:
    """Validate Bearer token against ``app.app_key`` (admin access).

    Accepts either ``Authorization: Bearer <key>`` header or ``?app_key=<key>``
    query parameter (the latter is needed for EventSource which cannot send headers).
    """
    key = get_admin_key()
    if not key:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Admin key is not configured."
        )

    token = _extract_bearer(authorization) or app_key
    if token is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Missing authentication token."
        )

    if not hmac.compare_digest(token, key):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid authentication token."
        )


async def verify_webui_key(
    authorization: str | None = Header(default=None),
) -> None:
    """Validate Bearer token for webui endpoints."""
    webui_key = get_webui_key()

    if not webui_key:
        if is_webui_enabled():
            return
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "WebUI access is disabled.")

    token = _extract_bearer(authorization)
    if token is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Missing authentication token."
        )

    if not hmac.compare_digest(token, webui_key):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid authentication token."
        )


__all__ = [
    "verify_api_key",
    "verify_admin_key",
    "verify_webui_key",
    "get_admin_key",
    "get_webui_key",
    "is_webui_enabled",
]
