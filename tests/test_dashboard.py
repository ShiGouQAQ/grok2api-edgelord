"""Dashboard endpoint aggregation tests."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.control.account.models import AccountRecord, RuntimeSnapshot
from app.main import app as main_app
from app.platform.audit.model import AuditRecord
from app.platform.audit.repository import AuditRepository


@pytest.fixture
def audit_repo(tmp_path):
    repo = AuditRepository(tmp_path / "audits.db")
    asyncio.run(repo.initialize())
    return repo


def _seed(audit_repo, *, count=4, tokens=40, model="grok-chat-auto", status=200):
    for i in range(count):
        asyncio.run(
            audit_repo.record(
                AuditRecord(
                    request_id=f"d{i}",
                    model=model if i % 2 == 0 else "grok-chat-fast",
                    provider="grok_web",
                    operation="chat",
                    status_code=status,
                    total_tokens=tokens,
                )
            )
        )


def _fake_directory():
    snapshot = RuntimeSnapshot(
        items=[
            AccountRecord(token="a1", pool="basic", provider="grok_web"),
            AccountRecord(token="a2", pool="super", provider="grok_web"),
            AccountRecord(token="b1", pool="super", provider="grok_build"),
        ]
    )
    repo = AsyncMock()
    repo.runtime_snapshot = AsyncMock(return_value=snapshot)
    return type("Directory", (), {"repository": repo})()


class TestDashboard:
    @pytest.fixture(autouse=True)
    def _wire_state(self, audit_repo, monkeypatch):
        main_app.state.audit_repo = audit_repo
        main_app.state.client_keys = AsyncMock()
        main_app.state.client_keys.count = AsyncMock(return_value=1)
        main_app.state.directory = _fake_directory()
        monkeypatch.setattr(
            "app.platform.auth.middleware.get_config", lambda *a, **k: "grok2api"
        )
        yield
        main_app.state.audit_repo = None
        main_app.state.directory = None

    def test_dashboard_shape(self):
        _seed(main_app.state.audit_repo)
        client = TestClient(main_app)
        resp = client.get(
            "/admin/api/dashboard", headers={"Authorization": "Bearer grok2api"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["period"] == "24h"
        assert body["generatedAt"]
        usage = body["usage"]
        assert usage["totalRequests"] == 4
        assert usage["successfulRequests"] == 4
        assert usage["failedRequests"] == 0
        assert usage["successRate"] == 100.0
        assert usage["totalTokens"] == 160
        assert len(body["series"]) == 1
        assert body["series"][0]["requests"] == 4
        assert body["series"][0]["tokens"] == 160
        assert len(body["topModels"]) == 2
        assert body["topModels"][0]["model"] == "grok-chat-auto"
        providers = {p["provider"]: p for p in body["providers"]}
        assert providers["grok_web"]["accounts"] == 2
        assert providers["grok_web"]["available"] == 2
        assert providers["grok_build"]["accounts"] == 1

    def test_dashboard_period_validation(self):
        client = TestClient(main_app)
        resp = client.get(
            "/admin/api/dashboard?period=1y",
            headers={"Authorization": "Bearer grok2api"},
        )
        assert resp.status_code == 400
        resp = client.get(
            "/admin/api/dashboard?timezone=Not/AZone",
            headers={"Authorization": "Bearer grok2api"},
        )
        assert resp.status_code == 400

    def test_dashboard_success_rate_with_failures(self):
        _seed(main_app.state.audit_repo, status=500)
        client = TestClient(main_app)
        resp = client.get(
            "/admin/api/dashboard", headers={"Authorization": "Bearer grok2api"}
        )
        body = resp.json()
        assert body["usage"]["successfulRequests"] == 0
        assert body["usage"]["failedRequests"] == 4
        assert body["usage"]["successRate"] == 0.0

    def test_dashboard_empty(self):
        client = TestClient(main_app)
        resp = client.get(
            "/admin/api/dashboard", headers={"Authorization": "Bearer grok2api"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["usage"]["totalRequests"] == 0
        assert body["series"] == []
        assert body["topModels"] == []
