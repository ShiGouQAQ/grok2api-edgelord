"""REAUTH_REQUIRED status: selection exclusion, manageability, recovery.

Port of chenyme (Go) AuthStatusReauthRequired: account is kept, excluded
from the selection pool, but still manageable (refresh candidate) and
recoverable on SUCCESS / RESTORE — unlike EXPIRED which is removed.
"""

from app.control.account.enums import AccountStatus, FeedbackKind
from app.control.account.models import AccountRecord
from app.control.account.state_machine import (
    AccountFeedback,
    apply_feedback,
    clear_failures,
    derive_status,
    is_manageable,
    is_selectable,
)


def _reauth_record() -> AccountRecord:
    """A REAUTH_REQUIRED account whose quota is valid (super pool, mode 0)."""
    return AccountRecord(
        token="reauth-test-token",
        pool="super",
        status=AccountStatus.REAUTH_REQUIRED,
        ext={"reauth_at": 1, "reauth_reason": "sso_build_preflight_failed"},
    )


def test_reauth_required_not_selectable():
    rec = _reauth_record()
    assert is_selectable(rec, 0) is False


def test_reauth_required_is_manageable():
    rec = _reauth_record()
    assert is_manageable(rec) is True


def test_derive_status_returns_reauth():
    rec = _reauth_record()
    assert derive_status(rec) == AccountStatus.REAUTH_REQUIRED


def test_clear_failures_clears_reauth_keys():
    rec = _reauth_record()
    updated = clear_failures(rec)
    assert updated.status == AccountStatus.ACTIVE
    assert "reauth_at" not in updated.ext
    assert "reauth_reason" not in updated.ext


def test_restore_feedback_clears_reauth_keys():
    rec = _reauth_record()
    updated = apply_feedback(rec, AccountFeedback(kind=FeedbackKind.RESTORE))
    assert updated.status == AccountStatus.ACTIVE
    assert "reauth_at" not in updated.ext
    assert "reauth_reason" not in updated.ext


def test_success_feedback_restores_reauth():
    rec = _reauth_record()
    updated = apply_feedback(rec, AccountFeedback(kind=FeedbackKind.SUCCESS))
    assert updated.status == AccountStatus.ACTIVE
    assert "reauth_at" not in updated.ext
    assert "reauth_reason" not in updated.ext


def test_rate_limited_does_not_downgrade_reauth():
    """RATE_LIMITED must not downgrade REAUTH_REQUIRED to COOLING — a
    cooldown expiring would otherwise derive the account back to ACTIVE
    while stale reauth keys linger (selection/status mismatch)."""
    rec = _reauth_record()
    updated = apply_feedback(rec, AccountFeedback(kind=FeedbackKind.RATE_LIMITED))
    assert updated.status == AccountStatus.REAUTH_REQUIRED
    assert "reauth_at" in updated.ext


def test_unauthorized_confirm_expired_clears_reauth_keys():
    """confirm_expired→EXPIRED removes stale reauth keys (no dual markers)."""
    rec = _reauth_record()
    updated = apply_feedback(
        rec,
        AccountFeedback(kind=FeedbackKind.UNAUTHORIZED, confirm_expired=True),
    )
    assert updated.status == AccountStatus.EXPIRED
    assert "reauth_at" not in updated.ext
    assert "reauth_reason" not in updated.ext
    assert "expired_at" in updated.ext
