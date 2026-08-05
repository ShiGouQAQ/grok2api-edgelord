"""Build OAuth token refresh — credential scheduling for grok.com build endpoint."""

import hashlib
from app.platform.logging.logger import logger

from app.platform.auth.oauth_device import (
    AccessDenied,
    DeviceFlowClient,
    ExpiredToken,
    TokenResponse,
)


#: In-flight manual (admin-initiated) retries, keyed ``<account>:manual-retry``.
#: Port of Go singleflight refreshKey += ":manual-retry" (ef10c4cb): a second
#: manual retry while one is in flight raises instead of issuing a second
#: OAuth request. # ponytail: process-lifetime set bounded by distinct keys
#: (manual retries are rare); weakrefs if churn ever matters.
_IN_FLIGHT_MANUAL_RETRIES: set[str] = set()


def compute_refresh_due_at(expires_at: float, account_id: str) -> float:
    """Compute when a Build OAuth token should be refreshed.

    Go upstream pattern: expires_at - 5min - jitter(account_id)
    where jitter = SHA256(account_id) % 180s.
    """
    jitter = int(hashlib.sha256(account_id.encode()).hexdigest(), 16) % 180
    return expires_at - 300.0 - float(jitter)


async def refresh_build_token(
    current_refresh_token: str,
    client: DeviceFlowClient | None = None,
) -> TokenResponse | None:
    """Refresh a Build OAuth token via DeviceFlowClient.

    Returns None for permanent failures (AccessDenied, ExpiredToken).
    """
    c = client or DeviceFlowClient()
    try:
        return await c.refresh_token(current_refresh_token)
    except AccessDenied:
        logger.warning("Build token refresh permanently denied")
        return None
    except ExpiredToken:
        logger.warning("Build refresh token expired")
        return None


def build_refresh_short_circuited(ext: dict, now_ms: int | float) -> bool:
    """Return True when the scheduler must NOT re-request OAuth.

    Port of Go resolvePermanentRefreshFailure (ef10c4cb): an account marked
    ``build_refresh_permanent`` with a still-alive access token is skipped by
    automatic paths. Manual (admin-initiated) retries bypass this via
    :func:`refresh_build_token_manual`.
    """
    if not ext.get("build_refresh_permanent"):
        return False
    return int(ext.get("build_expires_at") or 0) > now_ms


async def refresh_build_token_manual(
    account_key: str,
    current_refresh_token: str,
    client: DeviceFlowClient | None = None,
) -> TokenResponse | None:
    """Manual (admin-initiated) retry of a Build OAuth refresh.

    Port of Go ef10c4cb retryPermanentOnce: bypasses the permanent-refresh
    short-circuit exactly once even when the account was previously marked
    permanent-credential-rejected (``build_refresh_permanent``). After this
    single attempt the permanent status returns — the caller re-applies the
    marker when None comes back. Singleflight guard keyed with a
    ``:manual-retry`` suffix matches Go's ensureCredential refreshKey naming;
    a retry already in flight raises instead of issuing a second OAuth call.
    """
    key = f"{account_key}:manual-retry"
    if key in _IN_FLIGHT_MANUAL_RETRIES:
        raise RuntimeError(f"Build manual retry already in flight: {account_key}")
    _IN_FLIGHT_MANUAL_RETRIES.add(key)
    try:
        return await refresh_build_token(current_refresh_token, client)
    finally:
        _IN_FLIGHT_MANUAL_RETRIES.discard(key)
