"""Tests for app/dataplane/reverse/protocol/xai_billing.py."""

import asyncio
import base64
import json

import aiohttp
import pytest

from app.dataplane.reverse.protocol.xai_billing import (
    BuildBilling,
    fetch_build_billing,
    is_build_super,
    parse_billing,
    parse_subscription_tier,
    subscription_tier_from_jwt,
)
from app.platform.errors import UpstreamError


# ---------------------------------------------------------------------------
# parse_billing
# ---------------------------------------------------------------------------


def test_parse_billing_full():
    data = {
        "config": {"planCode": "supergrok", "planName": "SuperGrok"},
        "usage": {"monthlyLimit": 100, "used": 30},
        "onDemand": {"cap": 50, "used": 10},
        "prepaidBalance": 20,
    }
    b = parse_billing(data)
    assert b.plan_code == "supergrok"
    assert b.plan_name == "SuperGrok"
    assert b.monthly_limit == 100
    assert b.used == 30
    assert b.on_demand_cap == 50
    assert b.on_demand_used == 10
    assert b.prepaid_balance == 20
    assert b.is_paid


def test_parse_billing_free():
    b = parse_billing({"usage": {"monthlyLimit": 0, "used": 0}})
    assert not b.is_paid


def test_parse_billing_empty():
    b = parse_billing({})
    assert b.monthly_limit == 0
    assert not b.is_paid


# ---------------------------------------------------------------------------
# parse_subscription_tier
# ---------------------------------------------------------------------------


def test_parse_billing_real_upstream_shape():
    # Verified 2026-08-05 against cli-chat-proxy.grok.com/v1/billing with a
    # live minted Build OAuth token: values are {"val": int} objects.
    data = {
        "config": {
            "monthlyLimit": {"val": 0},
            "used": {"val": 0},
            "onDemandCap": {"val": 0},
            "billingPeriodStart": "2026-08-01T00:00:00+00:00",
            "history": [],
        }
    }
    b = parse_billing(data)
    assert b.monthly_limit == 0
    assert b.used == 0
    assert b.on_demand_cap == 0
    assert not b.is_paid


def test_parse_subscription_tier():
    assert (
        parse_subscription_tier({"subscription": {"plan": {"code": "supergrok"}}})
        == "supergrok"
    )
    assert parse_subscription_tier({"subscription": {"tier": "free"}}) == "free"


# ---------------------------------------------------------------------------
# subscription_tier_from_jwt
# ---------------------------------------------------------------------------


def _make_jwt(payload: dict) -> str:
    """Build a minimal unsigned JWT for testing."""
    header = (
        base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode())
        .rstrip(b"=")
        .decode()
    )
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{body}."


def test_subscription_tier_from_jwt():
    token = _make_jwt({"tier": 1})
    assert subscription_tier_from_jwt(token) == 1  # supergrok


def test_subscription_tier_from_jwt_free():
    token = _make_jwt({"tier": 0})
    assert subscription_tier_from_jwt(token) == 0  # free


def test_subscription_tier_from_jwt_invalid():
    assert subscription_tier_from_jwt("not-a-token") == -1


# ---------------------------------------------------------------------------
# is_build_super
# ---------------------------------------------------------------------------


def test_is_build_super_by_billing():
    assert is_build_super(
        billing=BuildBilling(plan_code="supergrok", monthly_limit=100)
    )


def test_is_build_super_by_entitlement():
    assert is_build_super(build_super_entitled=True)


def test_is_build_super_free():
    assert not is_build_super(billing=BuildBilling(plan_code="free"))


# ---------------------------------------------------------------------------
# fetch_build_billing
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status, payload=None, text_body=""):
        self.status = status
        self._payload = payload
        self._text = text_body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return self._text


class _FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _patch_config(monkeypatch, proxy_url="", skip_ssl_verify=False):
    """Patch snapshot.get_config; the function lazy-imports it at call time."""

    def fake_get_config(key=None, default=None):
        if key is None:

            class _Config:
                def get_str(self, k, d=""):
                    return proxy_url

                def get_bool(self, k, d=False):
                    return skip_ssl_verify

            return _Config()
        return default

    from app.platform.config import snapshot as _snap

    monkeypatch.setattr(_snap, "get_config", fake_get_config)


def test_fetch_build_billing_ok(monkeypatch):
    payload = {
        "config": {
            "monthlyLimit": {"val": 100},
            "used": {"val": 30},
            "onDemandCap": {"val": 50},
            "onDemandUsed": {"val": 10},
            "prepaidBalance": {"val": 20},
        }
    }
    session = _FakeSession(_FakeResponse(200, payload=payload))
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **kw: session)
    _patch_config(monkeypatch)

    b = asyncio.run(fetch_build_billing("tok-123"))

    assert b.plan_code == ""
    assert b.plan_name == ""
    assert b.monthly_limit == 100
    assert b.used == 30
    assert b.on_demand_cap == 50
    assert b.on_demand_used == 10
    assert b.prepaid_balance == 20
    assert b.is_paid
    url, kwargs = session.calls[0]
    assert url == "https://cli-chat-proxy.grok.com/v1/billing"
    assert kwargs["headers"] == {"Authorization": "Bearer tok-123"}
    assert kwargs["timeout"].total == 15.0


def test_fetch_build_billing_401(monkeypatch):
    session = _FakeSession(_FakeResponse(401, text_body="denied"))
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **kw: session)
    _patch_config(monkeypatch)

    with pytest.raises(UpstreamError) as ei:
        asyncio.run(fetch_build_billing("tok-123"))
    err = ei.value
    assert err.status == 401
    assert err.credential_rejected is True
    assert err.details["body"] == "denied"


def test_fetch_build_billing_403(monkeypatch):
    session = _FakeSession(_FakeResponse(403, text_body="forbidden"))
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **kw: session)
    _patch_config(monkeypatch)

    with pytest.raises(UpstreamError) as ei:
        asyncio.run(fetch_build_billing("tok-123"))
    err = ei.value
    assert err.status == 403
    assert err.credential_rejected is True
    assert err.details["body"] == "forbidden"


def test_fetch_build_billing_500(monkeypatch):
    session = _FakeSession(_FakeResponse(500, text_body="boom"))
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **kw: session)
    _patch_config(monkeypatch)

    with pytest.raises(UpstreamError) as ei:
        asyncio.run(fetch_build_billing("tok-123"))
    err = ei.value
    assert err.status == 500
    assert err.credential_rejected is False


def test_fetch_build_billing_connection_error(monkeypatch):
    # Transport errors propagate unwrapped (non-UpstreamError).
    session = _FakeSession(aiohttp.ClientConnectionError("conn refused"))
    monkeypatch.setattr(aiohttp, "ClientSession", lambda **kw: session)
    _patch_config(monkeypatch)

    with pytest.raises(aiohttp.ClientConnectionError):
        asyncio.run(fetch_build_billing("tok-123"))
