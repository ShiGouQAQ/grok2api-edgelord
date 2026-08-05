"""Tests for should_invalidate_build_forbidden — Build 403 invalidation rules.

All existing code-matching cases pass ``account_scoped=True``: the caller
(classifier) supplies the flag; the function gates on it (d00698ac wiring).
"""

from app.platform.errors import should_invalidate_build_forbidden


def test_invalidate_default_blocked_user():
    assert should_invalidate_build_forbidden(
        403, "blocked-user", "", account_scoped=True
    )


def test_invalidate_default_email_rejected():
    assert should_invalidate_build_forbidden(
        403, "", "email-domain-rejected", account_scoped=True
    )


def test_invalidate_not_403():
    assert not should_invalidate_build_forbidden(
        429, "blocked-user", "", account_scoped=True
    )


def test_invalidate_default_lowercase():
    # Case-insensitive: "BLOCKED-USER" matches default "blocked-user"
    assert should_invalidate_build_forbidden(
        403, "BLOCKED-USER", "", account_scoped=True
    )


def test_invalidate_account_suspended():
    assert should_invalidate_build_forbidden(
        403, "", "account suspended", account_scoped=True
    )


def test_invalidate_token_revoked():
    assert should_invalidate_build_forbidden(
        403, "", "token revoked", account_scoped=True
    )


def test_invalidate_user_is_blocked():
    assert should_invalidate_build_forbidden(
        403, "", "user is blocked", account_scoped=True
    )


def test_invalidate_no_match():
    assert not should_invalidate_build_forbidden(
        403, "random-code", "random message", account_scoped=True
    )


def test_invalidate_default_permission_denied():
    # Go config.go:685 default BuildForbiddenReauthCodes=["permission-denied"]
    assert should_invalidate_build_forbidden(403, "permission-denied", "")


def test_invalidate_exact_code_not_substring():
    # Code present → exact set membership only; message wording must not match.
    assert not should_invalidate_build_forbidden(
        403, "forbidden", "blocked-user", account_scoped=True
    )
    assert not should_invalidate_build_forbidden(
        403, "permission-denied-prefix", "", account_scoped=True
    )


def test_invalidate_code_match_does_not_require_prescoped():
    # Go service.go:895-897 forces AccountScoped=true after a code match — the
    # match itself establishes scope; no pre-requisite gate here.
    assert should_invalidate_build_forbidden(403, "blocked-user", "")
    assert should_invalidate_build_forbidden(403, "", "user is blocked")


def test_invalidate_safety_rejection_never_invalidates():
    # Content safety rejection shares permission-denied codes but is request-scoped.
    assert not should_invalidate_build_forbidden(
        403, "permission-denied", "", account_scoped=True, safety_rejected=True
    )
    assert not should_invalidate_build_forbidden(
        403, "blocked-user", "", account_scoped=True, safety_rejected=True
    )


def test_invalidate_custom_codes(monkeypatch):
    from app.platform.config import snapshot as _snap

    def fake_get_config(key=None, default=None):
        if key == "features.build_403_invalidation_codes":
            return "custom-code-1,custom-code-2"
        return default

    monkeypatch.setattr(_snap, "get_config", fake_get_config)
    from app.platform.errors import should_invalidate_build_forbidden as fn

    assert fn(403, "custom-code-1", "", account_scoped=True) is True
    assert fn(403, "", "custom-code-2", account_scoped=True) is True
    assert (
        fn(403, "blocked-user", "", account_scoped=True) is False
    )  # 自定义列表替换默认
    assert (
        fn(403, "custom-code-1", "", account_scoped=True, safety_rejected=True) is False
    )
