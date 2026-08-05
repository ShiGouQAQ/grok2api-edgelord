"""XAI Build billing/subscription tier parser.

Handles responses from:
- GET cli-chat-proxy.grok.com/v1/billing (Build OAuth access token)
- GET /user?include=subscription
- JWT token tier extraction
"""

from __future__ import annotations

import base64
import json
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import aiohttp

from app.platform.errors import UpstreamError

# ---------------------------------------------------------------------------
# BuildBilling dataclass
# ---------------------------------------------------------------------------


@dataclass
class BuildBilling:
    """Parsed billing info from GET /billing?format=credits."""

    plan_code: str = ""
    plan_name: str = ""
    monthly_limit: int = 0
    used: int = 0
    on_demand_cap: int = 0
    on_demand_used: int = 0
    prepaid_balance: int = 0

    @property
    def is_paid(self) -> bool:
        return bool(
            self.monthly_limit > 0 or self.on_demand_cap > 0 or self.prepaid_balance > 0
        )


# ---------------------------------------------------------------------------
# Billing parser
# ---------------------------------------------------------------------------


def _int_or(val: object, default: int = 0) -> int:
    """Safely coerce to int, falling back to *default*."""
    if val is None:
        return default
    try:
        return int(str(val))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _str_or(val: object, default: str = "") -> str:
    """Safely coerce to str, falling back to *default*."""
    if val is None:
        return default
    return str(val)


def _val(obj: object) -> int:
    """Coerce a real billing field. Values are either bare ints or
    ``{"val": int}`` objects (cli-chat-proxy /v1/billing responses)."""
    if isinstance(obj, dict):
        raw = obj.get("val")
        if raw is not None:
            return _int_or(raw)
    return _int_or(obj)


def parse_billing(data: dict[str, Any]) -> BuildBilling:
    """Parse the billing JSON response (cli-chat-proxy.grok.com /v1/billing).

    Real upstream shape (verified 2026-08-05 against a live minted token):
    ``config.monthlyLimit.val`` / ``config.used.val`` / ``config.onDemandCap.val``,
    plus ``?format=credits`` variant ``config.onDemandUsed.val`` and
    ``config.prepaidBalance.val``. Values are ``{"val": int}`` objects;
    bare-int legacy shapes are still tolerated.
    """
    config_raw = data.get("config")
    config: dict[str, Any] = config_raw if isinstance(config_raw, dict) else {}
    usage_raw = data.get("usage")
    usage: dict[str, Any] = usage_raw if isinstance(usage_raw, dict) else {}
    od_raw = data.get("onDemand")
    on_demand: dict[str, Any] = od_raw if isinstance(od_raw, dict) else {}

    return BuildBilling(
        plan_code=_str_or(config.get("planCode") or data.get("planCode")),
        plan_name=_str_or(config.get("planName") or data.get("planName")),
        monthly_limit=_val(
            config.get("monthlyLimit")
            or usage.get("monthlyLimit")
            or data.get("monthlyLimit")
        ),
        used=_val(config.get("used") or usage.get("used") or data.get("used")),
        on_demand_cap=_val(
            config.get("onDemandCap") or on_demand.get("cap") or data.get("onDemandCap")
        ),
        on_demand_used=_val(
            config.get("onDemandUsed")
            or on_demand.get("used")
            or data.get("onDemandUsed")
        ),
        prepaid_balance=_val(
            config.get("prepaidBalance")
            or data.get("prepaidBalance")
            or data.get("prepaid_balance")
        ),
    )


# ---------------------------------------------------------------------------
# Subscription tier parser
# ---------------------------------------------------------------------------


def parse_subscription_tier(data: dict[str, Any]) -> str:
    """Parse the /user response with ``include=subscription``.

    Extracts the tier string from:
    - ``subscription.plan.code``
    - ``subscription.plan.tier``
    - ``subscription.tier``
    """
    sub_raw = data.get("subscription")
    sub: dict[str, Any] = sub_raw if isinstance(sub_raw, dict) else {}
    plan_raw = sub.get("plan")
    plan: dict[str, Any] = plan_raw if isinstance(plan_raw, dict) else {}
    return _str_or(
        plan.get("code") or plan.get("tier") or sub.get("tier"),
        default="",
    )


# ---------------------------------------------------------------------------
# JWT tier extraction
# ---------------------------------------------------------------------------

_TIER_MAP: dict[int, str] = {
    0: "free",
    1: "supergrok",
    2: "x_basic",
    3: "x_premium",
    4: "x_premium_plus",
    5: "supergrok_heavy",
    6: "supergrok_lite",
}


def subscription_tier_from_jwt(id_token: str) -> int:
    """Decode JWT (no verification) and extract ``tier`` claim.

    Returns the numeric tier (0–6) or -1 if not parseable.
    """
    try:
        parts = id_token.split(".")
        if len(parts) < 2:
            return -1
        payload_b64 = parts[1]
        # Add padding
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        payload = json.loads(payload_bytes)
        tier = payload.get("tier")
        if tier is None:
            return -1
        return int(tier)
    except Exception:
        return -1


# ---------------------------------------------------------------------------
# Build super check
# ---------------------------------------------------------------------------


def is_build_super(
    *,
    billing: BuildBilling | None = None,
    build_super_entitled: bool = False,
) -> bool:
    """Return True if the account has Build Super access."""
    if build_super_entitled:
        return True
    return billing is not None and billing.is_paid


# ---------------------------------------------------------------------------
# Billing fetch
# ---------------------------------------------------------------------------


async def fetch_build_billing(
    access_token: str,
    *,
    timeout_s: float = 15.0,
    proxy_url: str | None = None,
) -> BuildBilling:
    """Fetch Build billing from the upstream XAI billing API.

    Returns parsed billing on 200. Raises UpstreamError with
    credential_rejected=True on 401/403; plain UpstreamError on other
    non-2xx statuses. Transport errors propagate as aiohttp exceptions.
    """
    from app.dataplane.proxy.adapters.session import normalize_proxy_url
    from app.platform.config.snapshot import get_config

    raw_proxy = (
        proxy_url
        if proxy_url is not None
        else str(get_config().get_str("proxy.egress.proxy_url", ""))
    )
    normalized = normalize_proxy_url(raw_proxy) if raw_proxy else None
    http_proxy: str | None = None
    connector: aiohttp.TCPConnector | None = None
    if normalized:
        ssl_ctx = ssl.create_default_context()
        if get_config().get_bool("proxy.egress.skip_ssl_verify", False):
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
        scheme = urlparse(normalized).scheme.lower()
        if scheme.startswith("socks"):
            from aiohttp_socks import ProxyConnector

            connector = ProxyConnector.from_url(normalized, ssl=ssl_ctx)
        else:
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            http_proxy = normalized

    try:
        async with (
            aiohttp.ClientSession(
                connector=connector, connector_owner=False
            ) as session,
            session.get(
                "https://cli-chat-proxy.grok.com/v1/billing",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=aiohttp.ClientTimeout(total=timeout_s),
                proxy=http_proxy,
            ) as resp,
        ):
            if resp.status == 200:
                return parse_billing(await resp.json())
            body = await resp.text()
            if resp.status in (401, 403):
                raise UpstreamError(
                    f"Build billing access denied: HTTP {resp.status}",
                    status=resp.status,
                    credential_rejected=True,
                    body=body,
                )
            raise UpstreamError(
                f"Build billing upstream error: HTTP {resp.status}",
                status=resp.status,
                body=body,
            )
    finally:
        if connector is not None:
            await connector.close()


__all__ = [
    "BuildBilling",
    "fetch_build_billing",
    "is_build_super",
    "parse_billing",
    "parse_subscription_tier",
    "subscription_tier_from_jwt",
]
