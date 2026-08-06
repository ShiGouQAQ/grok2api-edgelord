"""Admin model catalog API tests (Go model handler surface port).

Covers: list/groups/accounts/sync, enable/disable override persistence
(data/model_overrides.json), batch update, 501 delete stubs, /v1/models
hides admin-disabled models, chat validation rejects them.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from app.control.model import overrides, registry
from app.products.openai.router import _validate_chat
from app.products.openai.router import ChatCompletionRequest
from app.platform.errors import ValidationError

MODELS = registry.MODELS


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient with the override file redirected to tmp_path."""
    monkeypatch.setattr(overrides, "_PATH", tmp_path / "model_overrides.json")
    overrides._cache = None
    from app.main import app

    app.dependency_overrides.clear()
    tc = TestClient(app)
    yield tc
    app.dependency_overrides.clear()


def _admin(client: TestClient, method: str, path: str, **kwargs):
    kwargs.setdefault("headers", {"Authorization": "Bearer grok2api"})
    return getattr(client, method)(path, **kwargs)


class _FakeRecord:
    def __init__(self, pool: str) -> None:
        self.pool = pool

    def is_deleted(self) -> bool:
        return False


class _Repo:
    def __init__(self, records: list[_FakeRecord]) -> None:
        self._records = records

    async def runtime_snapshot(self):
        return type("Snapshot", (), {"items": self._records})()


def _model_names(items: list[dict]) -> set[str]:
    return {i["public_id"] for i in items}


# ---------------------------------------------------------------------------
# GET /admin/api/models
# ---------------------------------------------------------------------------


def test_list_models_paginated(client):
    resp = _admin(client, "get", "/admin/api/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == len(MODELS)
    assert len(body["items"]) == 20  # default pageSize
    assert body["page"] == 1
    first = body["items"][0]
    assert first["public_id"] in _model_names(body["items"])
    assert {"public_id", "provider", "capability", "enabled", "tier", "mode"} <= set(
        first
    )


def test_list_models_pagination_and_filters(client):
    resp = _admin(
        client, "get", "/admin/api/models", params={"page": 2, "pageSize": 10}
    )
    body = resp.json()
    assert body["page"] == 2
    assert len(body["items"]) == 10
    assert len({i["public_id"] for i in body["items"]}) == 10

    resp = _admin(client, "get", "/admin/api/models", params={"provider": "console"})
    body = resp.json()
    assert body["total"] > 0
    assert all(i["provider"] == "console" for i in body["items"])

    resp = _admin(client, "get", "/admin/api/models", params={"status": "disabled"})
    assert all(not i["enabled"] for i in resp.json()["items"])

    resp = _admin(client, "get", "/admin/api/models", params={"search": "multi-agent"})
    assert all("multi-agent" in i["public_id"] for i in resp.json()["items"])

    resp = _admin(client, "get", "/admin/api/models", params={"provider": "bogus"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /admin/api/models/groups
# ---------------------------------------------------------------------------


def test_model_groups(client):
    resp = _admin(client, "get", "/admin/api/models/groups")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] > 0
    keys = {g["key"] for g in body["items"]}
    assert any(k.startswith("grok_web:chat") for k in keys)
    assert any(k.startswith("console:") for k in keys)
    total_models = sum(g["count"] for g in body["items"])
    assert total_models >= len(MODELS)  # composite capabilities counted per cap


def test_model_groups_provider_filter(client):
    resp = _admin(
        client, "get", "/admin/api/models/groups", params={"provider": "grok_build"}
    )
    body = resp.json()
    assert all(g["provider"] == "grok_build" for g in body["items"])
    assert body["total"] > 0


# ---------------------------------------------------------------------------
# GET /admin/api/models/accounts
# ---------------------------------------------------------------------------


def test_model_accounts_counts_by_tier(client):
    repo = _Repo([_FakeRecord("basic"), _FakeRecord("basic"), _FakeRecord("super")])
    client.app.state.repository = repo
    resp = _admin(client, "get", "/admin/api/models/accounts")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items
    by_name = {i["public_id"]: i for i in items}
    # grok-4.3-console is BASIC tier → 2 basic accounts
    assert by_name["grok-4.3-console"]["supported_accounts"] == 2
    # grok-4.20-auto is SUPER tier → 1 super account
    assert by_name["grok-4.20-auto"]["supported_accounts"] == 1


def test_model_accounts_build_remote(client):
    repo = _Repo([_FakeRecord("build"), _FakeRecord("basic")])
    client.app.state.repository = repo
    with patch(
        "app.control.account.build_models.collect_build_remote_models",
        new=AsyncMock(return_value=["grok-x", "grok-y"]),
    ):
        resp = _admin(
            client,
            "get",
            "/admin/api/models/accounts",
            params={"provider": "grok_build"},
        )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert _model_names(items) == {"grok-x", "grok-y"}
    assert all(i["supported_accounts"] == 1 for i in items)


# ---------------------------------------------------------------------------
# POST /models (toggle), PATCH /models/{id}, PATCH /models/batch
# ---------------------------------------------------------------------------


def test_toggle_model_persists_override(client):
    target = "grok-4.20-fast"
    assert registry.is_enabled(target)

    resp = _admin(
        client, "post", "/admin/api/models", json={"model": target, "enabled": False}
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    # persisted to the override file + visible in admin listing
    assert overrides.load()[target]["enabled"] is False
    resp = _admin(client, "get", "/admin/api/models", params={"search": target})
    assert resp.json()["items"][0]["enabled"] is False
    assert registry.is_enabled(target) is False

    # re-enable via PATCH /models/{id}
    resp = _admin(
        client, "patch", f"/admin/api/models/{target}", json={"enabled": True}
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True
    assert registry.is_enabled(target) is True
    assert overrides.load()[target]["enabled"] is True


def test_patch_model_tier_and_revert(client):
    target = "grok-4.20-fast"
    resp = _admin(
        client, "patch", f"/admin/api/models/{target}", json={"tier": "heavy"}
    )
    assert resp.status_code == 200
    assert resp.json()["tier"] == "heavy"
    assert overrides.load()[target]["tier"] == "heavy"

    # null reverts the field to the static default
    resp = _admin(client, "patch", f"/admin/api/models/{target}", json={"tier": None})
    assert resp.status_code == 200
    assert resp.json()["tier"] == "basic"
    assert target not in overrides.load()  # empty delta → entry removed


def test_toggle_unknown_model_404(client):
    resp = _admin(
        client,
        "post",
        "/admin/api/models",
        json={"model": "no-such-model", "enabled": False},
    )
    assert resp.status_code == 404
    assert overrides.load() == {}


def test_batch_update(client):
    targets = ["grok-4.20-fast", "grok-4.3-fast"]
    resp = _admin(
        client,
        "patch",
        "/admin/api/models/batch",
        json={"ids": targets, "enabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == 2
    for name in targets:
        assert registry.is_enabled(name) is False

    resp = _admin(
        client,
        "patch",
        "/admin/api/models/batch",
        json={"ids": targets, "enabled": True},
    )
    assert resp.json()["updated"] == 2
    for name in targets:
        assert registry.is_enabled(name) is True


# ---------------------------------------------------------------------------
# POST /models/sync
# ---------------------------------------------------------------------------


def test_sync_non_build_noop(client):
    resp = _admin(
        client, "post", "/admin/api/models/sync", json={"provider": "grok_web"}
    )
    assert resp.status_code == 200
    assert resp.json()["synced"] == 0


def test_sync_build_rediscovery(client):
    repo = _Repo([])
    client.app.state.repository = repo
    with patch(
        "app.control.account.build_models.collect_build_remote_models",
        new=AsyncMock(return_value=["grok-a", "grok-b", "grok-c"]),
    ) as collect:
        resp = _admin(
            client, "post", "/admin/api/models/sync", json={"provider": "grok_build"}
        )
    assert resp.status_code == 200
    assert resp.json()["synced"] == 3
    assert resp.json()["models"] == ["grok-a", "grok-b", "grok-c"]
    collect.assert_called_once()


# ---------------------------------------------------------------------------
# DELETE stubs (no DB catalog)
# ---------------------------------------------------------------------------


def test_delete_models_501(client):
    resp = _admin(client, "delete", "/admin/api/models")
    assert resp.status_code == 501
    resp = _admin(client, "delete", "/admin/api/models/grok-4.20-fast")
    assert resp.status_code == 501


# ---------------------------------------------------------------------------
# Public listing + chat validation honor overrides
# ---------------------------------------------------------------------------


def test_public_v1_models_hides_disabled(client):
    import asyncio

    from app.control.account.enums import AccountStatus
    from app.control.account.models import AccountRecord
    from app.platform.config.snapshot import config, get_config

    asyncio.run(config.load())
    api_key = get_config("app.api_key", "")
    client.app.state.repository = _Repo(
        [
            AccountRecord(token="a", status=AccountStatus.ACTIVE, pool="basic"),
            AccountRecord(token="b", status=AccountStatus.ACTIVE, pool="super"),
        ]
    )
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    target = "grok-4.20-fast"
    before = {m["id"] for m in client.get("/v1/models", headers=headers).json()["data"]}
    assert target in before

    _admin(
        client, "post", "/admin/api/models", json={"model": target, "enabled": False}
    )
    after = {m["id"] for m in client.get("/v1/models", headers=headers).json()["data"]}
    assert target not in after
    assert before - after == {target}


def test_chat_validation_rejects_disabled(client):
    target = "grok-4.20-fast"
    _admin(
        client, "post", "/admin/api/models", json={"model": target, "enabled": False}
    )
    with pytest.raises(ValidationError) as exc:
        _validate_chat(
            ChatCompletionRequest(
                model=target, messages=[{"role": "user", "content": "hi"}]
            )
        )
    assert exc.value.code == "model_not_found"

    # re-enabled → validation passes
    _admin(client, "post", "/admin/api/models", json={"model": target, "enabled": True})
    _validate_chat(
        ChatCompletionRequest(
            model=target, messages=[{"role": "user", "content": "hi"}]
        )
    )
