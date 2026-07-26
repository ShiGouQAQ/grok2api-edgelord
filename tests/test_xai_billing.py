"""Tests for app/dataplane/reverse/protocol/xai_billing.py."""

import base64
import json

from app.dataplane.reverse.protocol.xai_billing import (
    BuildBilling,
    is_build_super,
    parse_billing,
    parse_subscription_tier,
    subscription_tier_from_jwt,
)


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
