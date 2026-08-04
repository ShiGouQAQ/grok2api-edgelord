"""REAUTH_REQUIRED status: manageability and status derivation.

Port of chenyme (Go) AuthStatusReauthRequired: account is kept, excluded
from the selection pool, but still manageable (refresh candidate) and
recoverable on refresh success — unlike EXPIRED which is removed.
"""

from app.control.account.enums import AccountStatus
from app.control.account.models import AccountRecord
from app.control.account.state_machine import derive_status, is_manageable


def _reauth_record() -> AccountRecord:
    """A REAUTH_REQUIRED account whose quota is valid (super pool, mode 0)."""
    return AccountRecord(
        token="reauth-test-token",
        pool="super",
        status=AccountStatus.REAUTH_REQUIRED,
        ext={"reauth_at": 1, "reauth_reason": "sso_build_preflight_failed"},
    )


def test_reauth_required_is_manageable():
    rec = _reauth_record()
    assert is_manageable(rec) is True


def test_derive_status_returns_reauth():
    rec = _reauth_record()
    assert derive_status(rec) == AccountStatus.REAUTH_REQUIRED
