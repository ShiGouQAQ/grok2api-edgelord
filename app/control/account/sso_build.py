"""SSO→Build credential conversion.

Port of Go web/sso_build.go: uses a grok.com SSO token to
automatically complete the Build OAuth Device Flow.
"""

import asyncio
import logging

import aiohttp

from app.platform.auth.oauth_device import DeviceFlowClient

CLIENT_ID = DeviceFlowClient.CLIENT_ID
DEVICE_URL = DeviceFlowClient.DEVICE_URL
TOKEN_URL = DeviceFlowClient.TOKEN_URL

logger = logging.getLogger(__name__)


async def convert_sso_to_build(sso_token: str) -> dict[str, str]:
    """Convert a grok.com SSO token to Build OAuth credentials.

    Uses the SSO cookie to authenticate with accounts.x.ai, then
    triggers and auto-completes the OAuth Device Flow.

    Returns a dict with access_token, refresh_token, id_token, expires_in.
    """
    headers = {
        "Cookie": f"sso={sso_token}",
        "User-Agent": "grok-shell/0.2.111",
        "Origin": "https://grok.com",
        "Referer": "https://grok.com/",
    }
    async with aiohttp.ClientSession(headers=headers) as session:
        # 1. Start device flow
        async with session.post(
            DEVICE_URL,
            data={
                "client_id": CLIENT_ID,
                "scope": "openid profile email offline_access grok-cli:access api:access",
                "referrer": "grok-build",
            },
        ) as resp:
            device = await resp.json()

        device_code = device["device_code"]

        # 2. Poll for token (SSO session should allow auto-approval)
        for attempt in range(60):
            async with session.post(
                TOKEN_URL,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": CLIENT_ID,
                    "device_code": device_code,
                },
            ) as resp:
                body = await resp.json()
                if "access_token" in body:
                    logger.info(
                        "SSO→Build conversion successful after %d poll(s)",
                        attempt + 1,
                    )
                    return {
                        "access_token": body["access_token"],
                        "refresh_token": body.get("refresh_token", ""),
                        "id_token": body.get("id_token", ""),
                        "expires_in": str(body.get("expires_in", 3600)),
                    }
                error = body.get("error", "")
                if error in ("access_denied", "expired_token"):
                    raise PermissionError(f"SSO→Build conversion denied: {error}")
            await asyncio.sleep(2)

        raise TimeoutError("SSO→Build conversion timed out after 120s")
