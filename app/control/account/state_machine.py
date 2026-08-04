"""Account lifecycle state machine helpers.

Status derivation (cooldown expiry) and manageability checks for
AccountRecord.  Feedback-driven state transitions are applied inline by
``refresh.py`` / backends via ``AccountPatch``; this module only exposes the
pure status predicates used across control-plane and product code.
"""

from app.platform.runtime.clock import now_ms

from .enums import AccountStatus
from .models import AccountRecord

_COOLDOWN_UNTIL_KEY = "cooldown_until"


def derive_status(record: AccountRecord, *, now: int | None = None) -> AccountStatus:
    """Compute the effective status, considering cooldown expiry."""
    if record.status != AccountStatus.COOLING:
        return record.status
    cooldown_until = record.ext.get(_COOLDOWN_UNTIL_KEY)
    if cooldown_until is None:
        return AccountStatus.COOLING
    ts = now if now is not None else now_ms()
    if ts >= int(cooldown_until):
        return AccountStatus.ACTIVE
    return AccountStatus.COOLING


def is_manageable(record: AccountRecord, *, now: int | None = None) -> bool:
    """Return True if the account should participate in maintenance flows."""
    if record.is_deleted():
        return False
    status = derive_status(record, now=now)
    return status in (
        AccountStatus.ACTIVE,
        AccountStatus.COOLING,
        AccountStatus.REAUTH_REQUIRED,
    )


__all__ = ["derive_status", "is_manageable"]
