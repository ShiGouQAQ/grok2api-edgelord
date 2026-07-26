"""Build OAuth token refresh — credential scheduling for grok.com build endpoint."""

import hashlib
import logging

from app.platform.auth.oauth_device import (
    AccessDenied,
    DeviceFlowClient,
    ExpiredToken,
    TokenResponse,
)

logger = logging.getLogger(__name__)


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
