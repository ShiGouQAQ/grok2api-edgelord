"""previous_response_id ownership-chain passthrough (Go service.go ownership chain).

- G4-C1: router forwards previous_response_id into the responses handler.
- G4-I1: console/build handlers force a single routing attempt when the id is
  present (Go: ownership != nil → newRoutingAttemptPolicy(1)).
- G4-I2: Console does not retain Response state (Go clears the id → stateless
  replay) — the decision is logged, never silent; no fabricated 404 without a
  responses store.
- G4-M1: build payload and grok.com chat payload carry previous_response_id
  upstream when provided (Go web/chat.go:172 passes it to openChat).
"""

import importlib
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock

import pytest

from app.control.model.enums import ModeId
from app.dataplane.reverse.protocol.xai_build import build_build_responses_payload
from app.dataplane.reverse.protocol.xai_chat import build_chat_payload
from app.products._routing_policy import (
    new_routing_attempt_policy,
    routing_attempt_policy,
)
from app.products.openai import build_responses as br
from app.products.openai import console_responses as cr
from app.products.openai import responses as responses_module
from app.products.openai.schemas import ResponsesCreateRequest

# `app.products.openai.router` re-exports the APIRouter instance; the module
# itself is only reachable via importlib.
router_module = importlib.import_module("app.products.openai.router")


class _FakeCfg:
    def get_bool(self, key, default=False):
        return default

    def get_str(self, key, default=""):
        return default

    def get_int(self, key, default=0):
        return default

    def get_float(self, key, default=0.0):
        return default

    def get_list(self, key, default=None):
        return default if default is not None else []


@pytest.fixture(autouse=True)
def _fake_snapshot_config(monkeypatch):
    """All modules under test read config via snapshot.get_config(key, default)."""

    def fake_get_config(key=None, default=None):
        if key is None:
            return _FakeCfg()
        return default

    from app.platform.config import snapshot as _snap

    monkeypatch.setattr(_snap, "get_config", fake_get_config)


class _FakeAccount:
    token = "tok_test"


@pytest.fixture
def _fake_account_dir(monkeypatch):
    """Handlers raise if the account directory is None; a dummy suffices here."""
    monkeypatch.setattr("app.dataplane.account._directory", object())


def _install_policy_spies(monkeypatch, module):
    """Replace the policy constructors in *module* with real-policy spies."""
    calls = {"new": [], "legacy": []}

    def _new(configured):
        policy = new_routing_attempt_policy(configured)
        calls["new"].append((configured, policy))
        return policy

    def _legacy(retries=None):
        policy = routing_attempt_policy(retries)
        calls["legacy"].append((retries, policy))
        return policy

    monkeypatch.setattr(module, "new_routing_attempt_policy", _new)
    monkeypatch.setattr(module, "routing_attempt_policy", _legacy)
    return calls


# ---------------------------------------------------------------------------
# G4-C1 — router passthrough
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_forwards_previous_response_id(monkeypatch):
    spec = type("Spec", (), {"enabled": True})()
    monkeypatch.setattr(router_module.model_registry, "get", lambda model: spec)
    captured: dict[str, Any] = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return {"id": "resp_ok"}

    monkeypatch.setattr(responses_module, "create", fake_create)

    req = ResponsesCreateRequest(
        model="grok-chat-auto", input="hi", previous_response_id="resp_abc"
    )
    from types import SimpleNamespace

    fake_request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(audit_repo=None)),
        state=SimpleNamespace(),
        url=SimpleNamespace(path="/v1/responses"),
    )
    await router_module.responses_endpoint(req, request=fake_request)

    assert captured["previous_response_id"] == "resp_abc"


# ---------------------------------------------------------------------------
# G4-I1 — console/build single-attempt policy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_console_create_single_attempt_when_previous_id(
    monkeypatch, _fake_account_dir
):
    calls = _install_policy_spies(monkeypatch, cr)

    await cr.create(
        model="grok-4.20-0309-console",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
        emit_think=True,
        temperature=0.8,
        top_p=0.95,
        response_id="resp_1",
        reasoning_id="rs_1",
        message_id="msg_1",
        previous_response_id="resp_abc",
    )

    assert len(calls["new"]) == 1
    assert calls["new"][0][0] == 1  # Go: ownership != nil → newRoutingAttemptPolicy(1)
    assert calls["legacy"] == []
    policy = calls["new"][0][1]
    assert policy.allows(0) is True
    assert policy.allows(1) is False  # exactly one attempt


@pytest.mark.asyncio
async def test_console_create_default_policy_without_previous_id(
    monkeypatch, _fake_account_dir
):
    calls = _install_policy_spies(monkeypatch, cr)
    monkeypatch.setattr(cr, "selection_max_retries", lambda: 5)

    await cr.create(
        model="grok-4.20-0309-console",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
        emit_think=True,
        temperature=0.8,
        top_p=0.95,
        response_id="resp_1",
        reasoning_id="rs_1",
        message_id="msg_1",
    )

    assert calls["new"] == []
    assert len(calls["legacy"]) == 1


@pytest.mark.asyncio
async def test_build_create_single_attempt_when_previous_id(
    monkeypatch, _fake_account_dir
):
    calls = _install_policy_spies(monkeypatch, br)

    await br.create(
        model="grok-chat-auto",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
        emit_think=True,
        temperature=0.8,
        top_p=0.95,
        response_id="resp_1",
        reasoning_id="rs_1",
        message_id="msg_1",
        previous_response_id="resp_abc",
    )

    assert len(calls["new"]) == 1
    assert calls["new"][0][0] == 1
    assert calls["legacy"] == []
    policy = calls["new"][0][1]
    assert policy.allows(0) is True
    assert policy.allows(1) is False


# ---------------------------------------------------------------------------
# G4-M1 — upstream forwarding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_create_forwards_previous_id_to_payload(
    monkeypatch, _fake_account_dir
):
    captured: dict[str, Any] = {}

    def fake_payload(**kwargs):
        captured.update(kwargs)
        return {"model": kwargs["model"], "input": [], "stream": True}

    monkeypatch.setattr(br, "build_build_responses_payload", fake_payload)
    monkeypatch.setattr(
        br, "reserve_account", AsyncMock(return_value=(_FakeAccount(), 0))
    )

    gen = await br.create(
        model="grok-chat-auto",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
        emit_think=True,
        temperature=0.8,
        top_p=0.95,
        response_id="resp_1",
        reasoning_id="rs_1",
        message_id="msg_1",
        previous_response_id="resp_abc",
    )
    assert isinstance(gen, AsyncGenerator)
    await gen.__anext__()  # payload is built before the first yield

    assert captured.get("previous_response_id") == "resp_abc"


def test_grok_chat_payload_carries_previous_response_id():
    payload = build_chat_payload(
        message="hi", mode_id=ModeId.AUTO, previous_response_id="resp_abc"
    )
    assert payload["previous_response_id"] == "resp_abc"


def test_grok_chat_payload_omits_when_absent():
    payload = build_chat_payload(message="hi", mode_id=ModeId.AUTO)
    assert "previous_response_id" not in payload


def test_build_payload_carries_previous_response_id():
    payload = build_build_responses_payload(
        model="grok-4",
        messages=[{"role": "user", "content": "hello"}],
        previous_response_id="resp_abc",
    )
    assert payload["previous_response_id"] == "resp_abc"


def test_build_payload_omits_when_absent():
    payload = build_build_responses_payload(
        model="grok-4", messages=[{"role": "user", "content": "hello"}]
    )
    assert "previous_response_id" not in payload
