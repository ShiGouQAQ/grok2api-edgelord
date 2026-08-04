"""Tests for should_invalidate_build_forbidden — Build 403 invalidation rules."""

from app.platform.errors import should_invalidate_build_forbidden


def test_invalidate_default_blocked_user():
    assert should_invalidate_build_forbidden(403, "blocked-user", "")


def test_invalidate_default_email_rejected():
    assert should_invalidate_build_forbidden(403, "", "email-domain-rejected")


def test_invalidate_not_403():
    assert not should_invalidate_build_forbidden(429, "blocked-user", "")


def test_invalidate_default_lowercase():
    # Case-insensitive: "BLOCKED-USER" matches default "blocked-user"
    assert should_invalidate_build_forbidden(403, "BLOCKED-USER", "")


def test_invalidate_account_suspended():
    assert should_invalidate_build_forbidden(403, "", "account suspended")


def test_invalidate_token_revoked():
    assert should_invalidate_build_forbidden(403, "", "token revoked")


def test_invalidate_user_is_blocked():
    assert should_invalidate_build_forbidden(403, "", "user is blocked")


def test_invalidate_no_match():
    assert not should_invalidate_build_forbidden(403, "random-code", "random message")


def test_invalidate_custom_codes(monkeypatch):
    from app.platform.config import snapshot as _snap

    def fake_get_config(key=None, default=None):
        if key == "features.build_403_invalidation_codes":
            return "custom-code-1,custom-code-2"
        return default

    monkeypatch.setattr(_snap, "get_config", fake_get_config)
    from app.platform.errors import should_invalidate_build_forbidden as fn

    assert fn(403, "custom-code-1", "") is True
    assert fn(403, "", "custom-code-2") is True
    assert fn(403, "blocked-user", "") is False  # 自定义列表替换默认
