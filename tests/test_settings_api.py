"""Structured settings API tests — Go→Python port of settings/handler.go.

Covers GET shape, PUT round-trip (with revision), stale-revision 409 and
unmappable-field 422. Config is isolated to a tmp TOML backend so PUT writes
never touch the real data/config.toml.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.platform.auth.middleware import verify_admin_key

_GO_SECTIONS = {
    "server",
    "providerBuild",
    "providerWeb",
    "providerConsole",
    "batch",
    "media",
    "frontend",
    "routing",
    "audit",
    "clientKeyDefaults",
    "accounts",
}


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


@pytest.fixture
def client():
    async def _mock_admin_key():
        return None

    app.dependency_overrides[verify_admin_key] = _mock_admin_key
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(verify_admin_key, None)


def test_settings_get_shape(client, isolated_config):
    resp = client.get("/admin/api/settings")
    assert resp.status_code == 200
    data = resp.json()

    assert set(data["config"]) == _GO_SECTIONS
    cfg = data["config"]
    # Spot-check representative mapped values (effective defaults).
    assert cfg["providerBuild"]["clientVersion"] == "0.2.119"
    assert cfg["providerBuild"]["tokenAuthConfigured"] is True
    assert cfg["providerWeb"]["chatTimeout"] == "60s"
    assert cfg["providerWeb"]["imageTimeout"] == "60s"
    assert cfg["providerWeb"]["videoTimeout"] == "60s"
    assert cfg["providerWeb"]["clearanceMode"] == "none"
    assert cfg["providerWeb"]["allowNSFW"] is True
    assert cfg["frontend"]["publicApiBaseURL"] == ""
    assert cfg["batch"]["refreshConcurrency"] == 50
    assert cfg["media"]["maxImageBytes"] == 0
    assert cfg["routing"]["preferFreeBuild"] is False
    assert isinstance(cfg["routing"]["segmentedSelector"], dict)
    assert cfg["accounts"]["buildForbiddenReauthCodes"] == []

    assert data["recommendedProviderBuild"] == {
        "clientVersion": "0.2.119",
        "userAgent": "",
    }
    assert isinstance(data["revision"], str) and data["revision"]
    assert isinstance(data["updatedAt"], str) and data["updatedAt"]
    assert data["restartRequired"] == []


def test_settings_put_round_trip(client, isolated_config):
    get_resp = client.get("/admin/api/settings")
    assert get_resp.status_code == 200
    original = get_resp.json()

    cfg = original["config"]
    cfg["providerWeb"]["chatTimeout"] = "45s"
    cfg["providerWeb"]["clearanceRefresh"] = "2h"
    cfg["batch"]["refreshConcurrency"] = 17
    cfg["routing"]["maxAttempts"] = 5
    cfg["frontend"]["publicApiBaseURL"] = "https://gw.example.com"
    cfg["accounts"]["buildForbiddenReauthCodes"] = ["foo", "bar"]

    put_resp = client.put(
        "/admin/api/settings",
        json={"revision": original["revision"], "config": cfg},
    )
    assert put_resp.status_code == 200, put_resp.text
    updated = put_resp.json()
    assert updated["revision"] != original["revision"]
    assert updated["config"]["providerWeb"]["chatTimeout"] == "45s"
    assert updated["config"]["frontend"]["publicApiBaseURL"] == "https://gw.example.com"
    assert updated["config"]["accounts"]["buildForbiddenReauthCodes"] == ["foo", "bar"]

    # The structured PUT must have landed in the real TOML sections.
    raw = client.get("/admin/api/config").json()
    assert raw["chat"]["timeout"] == 45
    assert raw["batch"]["refresh_concurrency"] == 17
    assert raw["proxy"]["clearance"]["refresh_interval"] == 7200
    assert raw["routing"]["max_routing_attempts"] == 5
    assert raw["app"]["app_url"] == "https://gw.example.com"
    assert raw["features"]["build_403_invalidation_codes"] == "foo,bar"


def test_settings_put_stale_revision_conflicts(client, isolated_config):
    get_resp = client.get("/admin/api/settings")
    original = get_resp.json()

    # Another session changes the config behind our back.
    import asyncio

    asyncio.run(
        isolated_config.apply_patch({"app": {"app_url": "https://other.example"}})
    )

    put_resp = client.put(
        "/admin/api/settings",
        json={"revision": original["revision"], "config": original["config"]},
    )
    assert put_resp.status_code == 409
    body = put_resp.json()
    assert body["error"]["code"] == "settingsConflict"


def test_settings_put_unmappable_field_rejected(client, isolated_config):
    get_resp = client.get("/admin/api/settings")
    original = get_resp.json()

    bad = dict(original["config"])
    bad["providerWeb"]["bogusField"] = 1  # unknown field in a known section
    bad["bogusSection"] = {"x": 1}  # unknown section

    put_resp = client.put(
        "/admin/api/settings",
        json={"revision": original["revision"], "config": bad},
    )
    assert put_resp.status_code == 422, put_resp.text
    body = put_resp.json()
    assert body["error"]["code"] == "unmappable_settings_fields"
    assert "providerWeb.bogusField" in body["error"]["message"]
    assert "bogusSection" in body["error"]["message"]

    # Fail closed: nothing was persisted.
    raw = client.get("/admin/api/config").json()
    assert "bogusSection" not in raw


def test_settings_put_computed_fields_ignored(client, isolated_config):
    """Derived flags (tokenAuthConfigured/statsigManualConfigured) are accepted but ignored."""
    get_resp = client.get("/admin/api/settings")
    original = get_resp.json()

    cfg = dict(original["config"])
    cfg["providerBuild"] = dict(cfg["providerBuild"], tokenAuthConfigured=False)
    cfg["providerWeb"] = dict(cfg["providerWeb"], statsigManualConfigured=True)

    put_resp = client.put(
        "/admin/api/settings",
        json={"revision": original["revision"], "config": cfg},
    )
    assert put_resp.status_code == 200, put_resp.text


def test_settings_put_missing_revision_bad_request(client, isolated_config):
    get_resp = client.get("/admin/api/settings")
    put_resp = client.put(
        "/admin/api/settings", json={"config": get_resp.json()["config"]}
    )
    assert put_resp.status_code == 400


def test_settings_put_invalid_duration_rejected(client, isolated_config):
    get_resp = client.get("/admin/api/settings")
    original = get_resp.json()

    cfg = dict(original["config"])
    cfg["providerWeb"] = dict(cfg["providerWeb"], chatTimeout="not-a-duration")

    put_resp = client.put(
        "/admin/api/settings",
        json={"revision": original["revision"], "config": cfg},
    )
    assert put_resp.status_code == 422
    assert "providerWeb.chatTimeout" in put_resp.json()["error"]["message"]
