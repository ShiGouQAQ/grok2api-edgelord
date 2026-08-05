"""OAuth 2.0 Device Authorization Grant client for auth.x.ai.

Implements the RFC 8628 Device Authorization Flow used by Grok Build
to authenticate CLI and headless clients against x.ai's OAuth server.
"""

from __future__ import annotations

import asyncio
from app.platform.logging.logger import logger
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import aiohttp

from app.platform.config.snapshot import get_config
from app.dataplane.proxy.adapters.session import normalize_proxy_url



# ─── Data classes ───────────────────────────────────────────────────────────


@dataclass
class DeviceCodeResponse:
    """Response from the device authorization endpoint."""

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str = ""
    interval: int = 5
    expires_in: int = 1800


@dataclass
class TokenResponse:
    """Response from the token endpoint."""

    access_token: str
    refresh_token: str = ""
    id_token: str = ""
    expires_in: int = 3600


# ─── Exceptions ─────────────────────────────────────────────────────────────


class AuthorizationPending(Exception):
    """The authorization request is still pending — keep polling."""


class SlowDown(Exception):
    """Server requests a slower polling rate."""


class AccessDenied(Exception):
    """The authorization was denied by the user."""


class ExpiredToken(Exception):
    """The device code has expired."""


# ─── Client ─────────────────────────────────────────────────────────────────


class DeviceFlowClient:
    """OAuth 2.0 Device Authorization Grant client for auth.x.ai."""

    DEVICE_URL = "https://auth.x.ai/oauth2/device/code"
    VERIFY_URL = "https://auth.x.ai/oauth2/device/verify"
    APPROVE_URL = "https://auth.x.ai/oauth2/device/approve"
    TOKEN_URL = "https://auth.x.ai/oauth2/token"
    CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
    SCOPE = (
        "openid profile email offline_access grok-cli:access "
        "api:access conversations:read conversations:write "
        "workspaces:read workspaces:write"
    )
    CLIENT_VERSION = "0.2.119"

    def __init__(
        self,
        client_id: str = CLIENT_ID,
        scope: str = SCOPE,
        client_version: str = CLIENT_VERSION,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.client_id = client_id
        self.scope = scope
        self.client_version = client_version
        self._session = session
        self._own_session = session is None
        self._proxy_url: str | None = (
            None  # cache: None=unresolved, ""=no proxy, else=normalized URL
        )
        self._http_proxy: str | None = None  # cache: proxy param for requests

    def _resolve_proxy_url(self) -> str | None:
        """Resolve and cache normalized proxy URL from config."""
        if self._proxy_url is not None:
            return self._proxy_url or None
        cfg = get_config()
        raw = str(cfg.get_str("proxy.egress.proxy_url", ""))
        normalized = normalize_proxy_url(raw) if raw else None
        self._proxy_url = normalized or ""
        return normalized

    def _build_connector(self) -> tuple[aiohttp.TCPConnector, str | None]:
        """Build connector and http_proxy following canonical proxy pattern.

        Result is cached for the session lifetime — _ensure_session calls this
        once and stores _http_proxy for _request to use without rebuilding.
        """
        proxy_url = self._resolve_proxy_url()
        if not proxy_url:
            return aiohttp.TCPConnector(), None

        ssl_ctx = ssl.create_default_context()
        cfg = get_config()
        if cfg.get_bool("proxy.egress.skip_ssl_verify", False):
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE

        scheme = urlparse(proxy_url).scheme.lower()
        if scheme.startswith("socks"):
            from aiohttp_socks import ProxyConnector

            return ProxyConnector.from_url(proxy_url, ssl=ssl_ctx), None
        return aiohttp.TCPConnector(ssl=ssl_ctx), proxy_url

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector, self._http_proxy = self._build_connector()
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=60),
            )
            self._own_session = True
        return self._session

    async def _request(
        self,
        method: str,
        url: str,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Send an HTTP request and return the parsed JSON body.

        Manages session lifecycle: creates a session if none was provided
        and closes it when done (only for internally-created sessions).
        Uses egress proxy if configured.
        """
        session = await self._ensure_session()
        try:
            async with session.request(
                method, url, data=data, headers=headers, proxy=self._http_proxy
            ) as resp:
                body: dict[str, Any] = await resp.json()
                if resp.status == 400:
                    error = body.get("error", "")
                    if error == "authorization_pending":
                        raise AuthorizationPending()
                    if error == "slow_down":
                        raise SlowDown()
                    if error == "access_denied":
                        raise AccessDenied()
                    if error == "expired_token":
                        raise ExpiredToken()
                    raise aiohttp.ClientResponseError(
                        resp.request_info,
                        resp.history,
                        status=resp.status,
                        message=str(body),
                    )
                resp.raise_for_status()
                return body
        finally:
            if self._own_session and self._session and not self._session.closed:
                await self._session.close()

    async def start_device(
        self,
        referrer: str = "grok-build",
    ) -> DeviceCodeResponse:
        """Begin the device authorization flow.

        Returns a DeviceCodeResponse with the user code and verification URI.
        """
        data = {
            "client_id": self.client_id,
            "scope": self.scope,
            "referrer": referrer,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "x-grok-client-version": self.client_version,
            "x-grok-client-surface": "ui",
        }
        body = await self._request(
            "POST",
            self.DEVICE_URL,
            data=data,
            headers=headers,
        )
        return DeviceCodeResponse(
            device_code=body["device_code"],
            user_code=body["user_code"],
            verification_uri=body["verification_uri"],
            verification_uri_complete=body.get("verification_uri_complete", ""),
            interval=body.get("interval", 5),
            expires_in=body.get("expires_in", 1800),
        )

    async def poll_device(
        self,
        device_code: str,
        interval: int = 5,
    ) -> TokenResponse:
        """Poll the token endpoint with a device code.

        Raises AuthorizationPending, SlowDown, AccessDenied, or ExpiredToken
        on the corresponding error responses.
        """
        data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": self.client_id,
            "device_code": device_code,
        }
        body = await self._request("POST", self.TOKEN_URL, data=data)
        return TokenResponse(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token", ""),
            id_token=body.get("id_token", ""),
            expires_in=body.get("expires_in", 3600),
        )

    async def refresh_token(self, refresh_token: str) -> TokenResponse:
        """Exchange a refresh token for a new access token."""
        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "refresh_token": refresh_token,
        }
        body = await self._request("POST", self.TOKEN_URL, data=data)
        return TokenResponse(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token", ""),
            id_token=body.get("id_token", ""),
            expires_in=body.get("expires_in", 3600),
        )

    async def poll_with_retry(
        self,
        device_code: str,
        interval: int = 5,
        timeout: int = 1800,
    ) -> TokenResponse:
        """Poll with automatic retry until success or timeout.

        Handles AuthorizationPending (sleep interval) and SlowDown
        (increase interval by 2s, max 30s). Raises TimeoutError if
        the total elapsed time exceeds *timeout* seconds.
        """
        elapsed = 0
        current_interval = interval
        while elapsed < timeout:
            try:
                return await self.poll_device(device_code, current_interval)
            except AuthorizationPending:
                pass
            except SlowDown:
                current_interval = min(current_interval + 2, 30)

            await asyncio.sleep(current_interval)
            elapsed += current_interval

        raise TimeoutError(f"Device authorization timed out after {timeout}s")
