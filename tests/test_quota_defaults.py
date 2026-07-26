"""Tests for quota_defaults — build mode integration."""

from app.control.account.quota_defaults import (
    _MODE_KEYS,
    _SUPPORTED_MODE_IDS_BY_POOL,
    BUILD_QUOTA_DEFAULTS,
    default_quota_set,
    supported_mode_ids,
)


def test_build_mode_key():
    assert _MODE_KEYS[6] == "quota_build"


def test_build_mode_in_all_pools():
    for pool in ("basic", "super", "heavy"):
        assert 6 in _SUPPORTED_MODE_IDS_BY_POOL[pool]


def test_build_mode_in_supported_ids():
    for pool in ("basic", "super", "heavy"):
        ids = supported_mode_ids(pool)
        assert 6 in ids


def test_default_quota_set_has_build():
    for pool in ("basic", "super", "heavy"):
        qs = default_quota_set(pool)
        assert qs.quota_build is not None
        assert qs.quota_build.total == 100


def test_build_quota_defaults():
    qs = BUILD_QUOTA_DEFAULTS
    assert qs.quota_build is not None
    assert qs.quota_build.total == 100
    assert qs.fast.total == 30
