"""System + health endpoint tests — Go→Python port of system/handler.go + server.go."""

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.platform.auth.middleware import verify_admin_key
from app.platform.meta import get_project_version


@pytest.fixture
def client():
    async def _mock_admin_key():
        return None

    app.dependency_overrides[verify_admin_key] = _mock_admin_key
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(verify_admin_key, None)


@pytest.fixture
def isolated_config(tmp_path):
    """Point the config singleton at a throwaway TOML backend."""
    from app.platform.config.backends.toml import TomlConfigBackend
    from app.platform.config.snapshot import config

    old_backend, old_loaded = config._backend, config._loaded
    config._backend = TomlConfigBackend(tmp_path / "config.toml")
    config._loaded = False
    config._version = None
    yield config._backend
    config._backend = old_backend
    config._loaded = old_loaded
    config._version = None


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_ok(client):
    import app.dataplane.account as dataplane_account

    old_repo = getattr(app.state, "repository", None)
    old_directory = dataplane_account._directory
    app.state.repository = object()
    setattr(dataplane_account, "_directory", object())
    try:
        resp = client.get("/readyz")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "ready": True,
            "state": "ready",
            "components": {
                "config": "ok",
                "database": "ok",
                "account_directory": "ok",
            },
        }
    finally:
        if old_repo is None:
            app.state.repository = None
        else:
            app.state.repository = old_repo
        dataplane_account._directory = old_directory


def test_readyz_not_ready(client):
    import app.dataplane.account as dataplane_account

    old_repo = getattr(app.state, "repository", None)
    old_directory = dataplane_account._directory
    app.state.repository = None
    setattr(dataplane_account, "_directory", None)
    try:
        resp = client.get("/readyz")
        assert resp.status_code == 503
        body = resp.json()
        assert body["ready"] is False
        assert body["state"] == "not_ready"
        assert body["components"]["database"] == "error"
        assert body["components"]["account_directory"] == "error"
    finally:
        if old_repo is not None:
            app.state.repository = old_repo
        dataplane_account._directory = old_directory


def test_system_public_api_base_url(client, isolated_config):
    import asyncio

    asyncio.run(
        isolated_config.apply_patch({"app": {"app_url": "https://gw.example.com/"}})
    )
    resp = client.get("/admin/api/system")
    assert resp.status_code == 200
    # Trailing slash trimmed, matching Go (strings.TrimRight(baseURL, "/")).
    assert resp.json() == {"publicApiBaseURL": "https://gw.example.com"}


def test_system_version(client, monkeypatch, tmp_path):
    import app.products.web.admin.system as system_mod

    cache = tmp_path / "version_check.json"
    cache.write_text(
        json.dumps({"latestVersion": "9.9.9", "changelog": "lots of fixes"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(system_mod, "_VERSION_CHECK_PATH", cache)

    resp = client.get("/admin/api/system/version")
    assert resp.status_code == 200
    body = resp.json()
    assert body["currentVersion"] == get_project_version()
    assert body["latestVersion"] == "9.9.9"
    assert body["changelog"] == "lots of fixes"


def test_system_version_no_cache(client, monkeypatch, tmp_path):
    import app.products.web.admin.system as system_mod

    monkeypatch.setattr(system_mod, "_VERSION_CHECK_PATH", tmp_path / "missing.json")

    resp = client.get("/admin/api/system/version")
    body = resp.json()
    assert body["currentVersion"] == get_project_version()
    assert body["latestVersion"] == body["currentVersion"]  # falls back to current
    assert body["changelog"] == ""


def test_system_update_check_stub(client):
    resp = client.post("/admin/api/system/update/check")
    assert resp.status_code == 200
    body = resp.json()
    assert body["currentVersion"] == get_project_version()
    assert body["updateAvailable"] is False
    assert isinstance(body["latestVersion"], str)
    assert isinstance(body["changelog"], str)
