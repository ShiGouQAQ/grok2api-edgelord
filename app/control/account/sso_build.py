"""SSO→Build credential conversion.

Port of Go web/sso_build.go: uses a grok.com SSO token to
automatically complete the Build OAuth Device Flow.
"""

import asyncio
import logging
import ssl
from urllib.parse import urlparse

import aiohttp

from app.platform.auth.oauth_device import DeviceFlowClient
from app.platform.config.snapshot import get_config
from app.dataplane.proxy.adapters.session import normalize_proxy_url

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
    cfg = get_config()
    raw_proxy = str(cfg.get_str("proxy.egress.proxy_url", ""))
    proxy_url = normalize_proxy_url(raw_proxy) if raw_proxy else None

    # Canonical SSL context: respect skip_ssl_verify config
    ssl_ctx = ssl.create_default_context()
    if proxy_url and cfg.get_bool("proxy.egress.skip_ssl_verify", False):
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

    headers = {
        "Cookie": f"sso={sso_token}",
        "User-Agent": "grok-shell/0.2.111",
        "Origin": "https://grok.com",
        "Referer": "https://grok.com/",
    }

    # For SOCKS proxies, use ProxyConnector; for HTTP proxies, pass proxy= param
    scheme = urlparse(proxy_url).scheme.lower() if proxy_url else ""
    if proxy_url and scheme.startswith("socks"):
        from aiohttp_socks import ProxyConnector

        connector = ProxyConnector.from_url(proxy_url, ssl=ssl_ctx)
        http_proxy = None
    else:
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        http_proxy = proxy_url

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(
        headers=headers, connector=connector, timeout=timeout
    ) as session:
        # 1. Start device flow
        async with session.post(
            DEVICE_URL,
            data={
                "client_id": CLIENT_ID,
                "scope": "openid profile email offline_access grok-cli:access api:access",
                "referrer": "grok-build",
            },
            proxy=http_proxy,
        ) as resp:
            if resp.status != 200:
                raise RuntimeError(
                    f"Device code request failed ({resp.status}): {(await resp.text())[:200]}"
                )
            device = await resp.json()

        device_code = device.get("device_code")
        if not device_code:
            raise ValueError(f"No device_code in response: {device}")
        poll_interval = int(device.get("interval", 5))

        # 2. Poll for token (SSO session should allow auto-approval)
        logger.info(
            "SSO→Build device flow started: user_code={} device_code={} interval={}s",
            device.get("user_code", "?"),
            device_code,
            poll_interval,
        )
        for attempt in range(60):
            async with session.post(
                TOKEN_URL,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": CLIENT_ID,
                    "device_code": device_code,
                },
                proxy=http_proxy,
            ) as resp:
                if resp.status not in (200, 400):
                    body_snippet = (await resp.text())[:200]
                    logger.warning(
                        "SSO→Build poll attempt=%d unexpected_status=%d body=%s",
                        attempt + 1,
                        resp.status,
                        body_snippet,
                    )
                    raise RuntimeError(
                        f"Token poll failed ({resp.status}): {body_snippet}"
                    )
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
                if error == "slow_down":
                    logger.info(
                        "SSO→Build poll attempt=%d slow_down, backing off", attempt + 1
                    )
                    poll_interval = min(poll_interval + 2, 30)
                elif error in ("access_denied", "expired_token"):
                    logger.warning(
                        "SSO→Build poll attempt=%d terminal_error=%s",
                        attempt + 1,
                        error,
                    )
                    raise PermissionError(f"SSO→Build conversion denied: {error}")
                elif error == "authorization_pending":
                    if attempt == 0 or (attempt + 1) % 10 == 0:
                        logger.info(
                            "SSO→Build poll attempt=%d still pending (interval=%ds)",
                            attempt + 1,
                            poll_interval,
                        )
                else:
                    logger.info(
                        "SSO→Build poll attempt=%d status=%s body=%s",
                        attempt + 1,
                        error or "unknown",
                        str(body)[:200],
                    )
            await asyncio.sleep(poll_interval)

        raise TimeoutError("SSO→Build conversion timed out after 120s")
