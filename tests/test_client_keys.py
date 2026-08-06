"""Client key tests — CRUD, scopes, admin endpoints, auth integration + limits."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import APIRouter, Depends, Request
from fastapi.testclient import TestClient

from app.control.clientkey.repository import ClientKeyRepository
from app.control.clientkey.service import (
    ClientKeyService,
    CreateInput,
    InvalidInputError,
    NotFoundError,
    parse_rfc3339_ms,
    resolve_scopes,
)
from app.main import app as main_app
from app.platform.auth.middleware import _active, _rpm_windows, verify_api_key


@pytest.fixture
def ck_repo(tmp_path):
    repo = ClientKeyRepository(tmp_path / "keys.db")
    asyncio.run(repo.initialize())
    return repo


@pytest.fixture(autouse=True)
def _clean_rate_limits():
    yield
    _rpm_windows.clear()
    _active.clear()


# ═══════════════════════════════════════════════════════════════════════════
# Service / repository CRUD
# ═══════════════════════════════════════════════════════════════════════════


class TestClientKeyService:
    def test_create_generates_secret_and_prefix(self, ck_repo):
        svc = ClientKeyService(ck_repo)
        result = asyncio.run(svc.create(CreateInput(name="my key")))
        assert result.secret.startswith("grok2api_")
        assert len(result.secret) == 41  # grok2api_ + 32 hex
        assert result.key.prefix == result.secret[:17]
        assert result.key.name == "my key"
        assert result.key.enabled is True
        assert result.key.rpm_limit == 120  # Go DefaultRPMLimit
        assert result.key.max_concurrent == 8  # Go DefaultMaxConcurrent
        assert result.key.created_at > 0

    def test_create_zero_limits_mean_unlimited(self, ck_repo):
        svc = ClientKeyService(ck_repo)
        result = asyncio.run(
            svc.create(CreateInput(name="u", rpm_limit=0, max_concurrent=0))
        )
        assert result.key.rpm_limit == 0
        assert result.key.max_concurrent == 0

    def test_create_requires_name(self, ck_repo):
        svc = ClientKeyService(ck_repo)
        with pytest.raises(InvalidInputError):
            asyncio.run(svc.create(CreateInput(name="  ")))

    def test_create_rejects_past_expiry(self, ck_repo):
        svc = ClientKeyService(ck_repo)
        past = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        with pytest.raises(InvalidInputError):
            asyncio.run(svc.create(CreateInput(name="x", expires_at=past)))

    def test_create_rejects_bad_rpm(self, ck_repo):
        svc = ClientKeyService(ck_repo)
        with pytest.raises(InvalidInputError):
            asyncio.run(svc.create(CreateInput(name="x", rpm_limit=100_001)))

    def test_get_by_prefix_roundtrip(self, ck_repo):
        svc = ClientKeyService(ck_repo)
        result = asyncio.run(svc.create(CreateInput(name="k")))
        found = asyncio.run(ck_repo.get_by_prefix(result.key.prefix))
        assert found is not None
        assert found.secret == result.secret
        assert found.id == result.key.id

    def test_update_fields(self, ck_repo):
        svc = ClientKeyService(ck_repo)
        result = asyncio.run(svc.create(CreateInput(name="k", rpm_limit=10)))
        key = asyncio.run(
            svc.update(
                result.key.id,
                name="renamed",
                enabled=False,
                rpm_limit=20,
                allowed_model_ids=["grok-4.20-auto"],
            )
        )
        assert key.name == "renamed"
        assert key.enabled is False
        assert key.rpm_limit == 20
        assert key.allowed_model_ids == ["grok-4.20-auto"]

    def test_update_clear_expires_at(self, ck_repo):
        svc = ClientKeyService(ck_repo)
        future = int(datetime(2099, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        result = asyncio.run(svc.create(CreateInput(name="k", expires_at=future)))
        key = asyncio.run(svc.update(result.key.id, clear_expires_at=True))
        assert key.expires_at is None

    def test_delete_missing_raises_not_found(self, ck_repo):
        svc = ClientKeyService(ck_repo)
        with pytest.raises(NotFoundError):
            asyncio.run(svc.delete(9999))

    def test_batch_operations(self, ck_repo):
        svc = ClientKeyService(ck_repo)
        a = asyncio.run(svc.create(CreateInput(name="a")))
        b = asyncio.run(svc.create(CreateInput(name="b")))
        ids = [a.key.id, b.key.id]
        assert asyncio.run(ck_repo.batch_set_enabled(ids, False)) == 2
        for key_id in ids:
            assert asyncio.run(ck_repo.get(key_id)).enabled is False
        assert asyncio.run(ck_repo.batch_delete([a.key.id])) == 1
        assert asyncio.run(ck_repo.get(a.key.id)) is None

    def test_list_search_and_status(self, ck_repo):
        svc = ClientKeyService(ck_repo)
        asyncio.run(svc.create(CreateInput(name="alpha")))
        asyncio.run(svc.create(CreateInput(name="beta", enabled=False)))
        items, total = asyncio.run(ck_repo.list_keys(search="alp"))
        assert total == 1 and items[0].name == "alpha"
        items, total = asyncio.run(ck_repo.list_keys(status="disabled"))
        assert total == 1 and items[0].name == "beta"
        items, total = asyncio.run(ck_repo.list_keys(page=1, page_size=1))
        assert len(items) == 1 and total == 2

    def test_parse_rfc3339_ms(self):
        ms = parse_rfc3339_ms("2026-01-02T03:04:05Z")
        assert ms == int(
            datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc).timestamp() * 1000
        )
        assert parse_rfc3339_ms("") is None

    def test_resolve_scopes_legacy_pool(self):
        assert resolve_scopes([], [], "super") == (["all"], ["super"])
        assert resolve_scopes([], [], "free") == (["all"], ["free"])
        with pytest.raises(InvalidInputError):
            resolve_scopes([], [], "bogus")
        with pytest.raises(InvalidInputError):
            resolve_scopes(["all"], [], "super")  # 不能同时设置

    def test_resolve_scopes_provider_combos(self):
        assert resolve_scopes(["grok_build", "grok_web"], [], None) == (
            ["grok_build", "grok_web"],
            [],
        )
        assert resolve_scopes(["all"], ["all"], None) == (["all"], ["all"])
        with pytest.raises(InvalidInputError):
            resolve_scopes(["bogus"], [], None)


# ═══════════════════════════════════════════════════════════════════════════
# Auth integration (verify_api_key + client keys)
# ═══════════════════════════════════════════════════════════════════════════


def _auth_app(ck_repo):
    inner = APIRouter()

    @inner.get("/probe")
    async def probe(request: Request):
        return {
            "kind": request.state.auth_kind,
            "key_id": getattr(request.state, "client_key_id", None),
        }

    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.state.client_keys = ck_repo
    test_app.include_router(inner, dependencies=[Depends(verify_api_key)])
    return test_app


class TestAuthIntegration:
    def test_valid_client_key_passes_and_sets_identity(self, ck_repo, monkeypatch):
        monkeypatch.setattr(
            "app.platform.auth.middleware.get_config", lambda *a, **k: "admin-key"
        )
        svc = ClientKeyService(ck_repo)
        result = asyncio.run(svc.create(CreateInput(name="k")))
        client = TestClient(_auth_app(ck_repo))
        resp = client.get(
            "/probe", headers={"Authorization": f"Bearer {result.secret}"}
        )
        assert resp.status_code == 200
        assert resp.json()["kind"] == "client_key"
        assert resp.json()["key_id"] == result.key.id

    def test_app_api_key_still_works(self, ck_repo, monkeypatch):
        monkeypatch.setattr(
            "app.platform.auth.middleware.get_config", lambda *a, **k: "admin-key"
        )
        client = TestClient(_auth_app(ck_repo))
        resp = client.get("/probe", headers={"Authorization": "Bearer admin-key"})
        assert resp.status_code == 200
        assert resp.json()["kind"] == "app_api_key"

    def test_wrong_secret_rejected_when_key_configured(self, ck_repo, monkeypatch):
        monkeypatch.setattr(
            "app.platform.auth.middleware.get_config", lambda *a, **k: "admin-key"
        )
        svc = ClientKeyService(ck_repo)
        result = asyncio.run(svc.create(CreateInput(name="k")))
        evil = result.secret[:-1] + ("0" if result.secret[-1] != "0" else "1")
        client = TestClient(_auth_app(ck_repo))
        resp = client.get("/probe", headers={"Authorization": f"Bearer {evil}"})
        assert resp.status_code == 403

    def test_open_mode_passes_garbage_but_rejects_fake_client_key(
        self, ck_repo, monkeypatch
    ):
        monkeypatch.setattr(
            "app.platform.auth.middleware.get_config", lambda *a, **k: ""
        )
        client = TestClient(_auth_app(ck_repo))
        assert (
            client.get(
                "/probe", headers={"Authorization": "Bearer garbage-token"}
            ).status_code
            == 200
        )  # open mode
        resp = client.get(
            "/probe", headers={"Authorization": "Bearer grok2api_deadbeef"}
        )
        assert resp.status_code == 403  # 伪造 client key 不放过

    def test_disabled_key_rejected(self, ck_repo, monkeypatch):
        monkeypatch.setattr(
            "app.platform.auth.middleware.get_config", lambda *a, **k: ""
        )
        svc = ClientKeyService(ck_repo)
        result = asyncio.run(svc.create(CreateInput(name="k")))
        asyncio.run(svc.update(result.key.id, enabled=False))
        client = TestClient(_auth_app(ck_repo))
        resp = client.get(
            "/probe", headers={"Authorization": f"Bearer {result.secret}"}
        )
        assert resp.status_code == 403

    def test_rpm_limit_returns_429(self, ck_repo, monkeypatch):
        monkeypatch.setattr(
            "app.platform.auth.middleware.get_config", lambda *a, **k: ""
        )
        svc = ClientKeyService(ck_repo)
        result = asyncio.run(svc.create(CreateInput(name="k", rpm_limit=2)))
        client = TestClient(_auth_app(ck_repo))
        headers = {"Authorization": f"Bearer {result.secret}"}
        assert client.get("/probe", headers=headers).status_code == 200
        assert client.get("/probe", headers=headers).status_code == 200
        assert client.get("/probe", headers=headers).status_code == 429

    def test_max_concurrent_returns_429(self, ck_repo, monkeypatch):
        monkeypatch.setattr(
            "app.platform.auth.middleware.get_config", lambda *a, **k: ""
        )
        svc = ClientKeyService(ck_repo)
        result = asyncio.run(svc.create(CreateInput(name="k", max_concurrent=1)))
        headers = {"Authorization": f"Bearer {result.secret}"}

        slow_app = _auth_app(ck_repo)

        @slow_app.get("/slow", dependencies=[Depends(verify_api_key)])
        async def slow(request: Request):
            await asyncio.sleep(0.5)
            return {"ok": True}

        import httpx

        async def run():
            transport = httpx.ASGITransport(app=slow_app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                r1 = asyncio.create_task(client.get("/slow", headers=headers))
                await asyncio.sleep(0.1)  # 确保 r1 先持有并发槽
                r2 = asyncio.create_task(client.get("/slow", headers=headers))
                return sorted([(await r1).status_code, (await r2).status_code])

        assert asyncio.run(run()) == [200, 429]


# ═══════════════════════════════════════════════════════════════════════════
# Admin endpoints
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def admin_client(ck_repo, monkeypatch):
    monkeypatch.setattr(
        "app.platform.auth.middleware.get_config", lambda *a, **k: "grok2api"
    )
    main_app.state.client_keys = ck_repo
    main_app.state.client_key_service = ClientKeyService(ck_repo)
    return TestClient(main_app)


class TestAdminEndpoints:
    def _auth(self):
        return {"Authorization": "Bearer grok2api"}

    def test_create_list_reveal_update_delete(self, admin_client):
        headers = self._auth()
        resp = admin_client.post(
            "/admin/api/client-keys",
            json={"name": "demo", "rpmLimit": 30, "maxConcurrent": 5},
            headers=headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["key"]["id"] == str(body["key"]["id"])
        assert body["secret"].startswith("grok2api_")
        assert body["key"]["rpmLimit"] == 30
        assert body["key"]["providerScope"] == ["all"]
        key_id = body["key"]["id"]

        resp = admin_client.get("/admin/api/client-keys", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

        resp = admin_client.get(
            f"/admin/api/client-keys/{key_id}/secret", headers=headers
        )
        assert resp.status_code == 200
        assert resp.json()["secret"] == body["secret"]

        resp = admin_client.patch(
            f"/admin/api/client-keys/{key_id}",
            json={"enabled": False, "expiresAt": ""},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        assert resp.json()["expiresAt"] is None

        resp = admin_client.patch(
            "/admin/api/client-keys/batch",
            json={"ids": [key_id], "enabled": True},
            headers=headers,
        )
        assert resp.json()["updated"] == 1

        resp = admin_client.delete(f"/admin/api/client-keys/{key_id}", headers=headers)
        assert resp.json()["deleted"] is True

    def test_create_with_account_pool_and_scopes(self, admin_client):
        headers = self._auth()
        resp = admin_client.post(
            "/admin/api/client-keys",
            json={"name": "legacy", "accountPool": "super"},
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["key"]["tierScope"] == ["super"]

        resp = admin_client.post(
            "/admin/api/client-keys",
            json={
                "name": "scoped",
                "providerScope": ["grok_build", "grok_web"],
                "tierScope": ["free"],
                "allowedModelIds": ["grok-4.20-fast"],
            },
            headers=headers,
        )
        assert resp.status_code == 201
        key = resp.json()["key"]
        assert key["providerScope"] == ["grok_build", "grok_web"]
        assert key["tierScope"] == ["free"]
        assert key["allowedModelIds"] == ["grok-4.20-fast"]

    def test_invalid_scopes_400(self, admin_client):
        headers = self._auth()
        resp = admin_client.post(
            "/admin/api/client-keys",
            json={"name": "x", "providerScope": ["bogus"]},
            headers=headers,
        )
        assert resp.status_code == 400

    def test_not_found_404(self, admin_client):
        headers = self._auth()
        resp = admin_client.delete("/admin/api/client-keys/999999", headers=headers)
        assert resp.status_code == 404

