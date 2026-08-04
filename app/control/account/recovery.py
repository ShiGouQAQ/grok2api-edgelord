"""Bounded recovery for accounts stuck in REAUTH_REQUIRED.

A genuinely-dead SSO account fails scheduled refresh forever and stays
REAUTH_REQUIRED (the state is refresh-manageable, so refresh keeps retrying
the dead credential).  Consecutive credential failures are counted in
``ext.reauth_fail_count`` — bumped on each reauth marking while the account is
already REAUTH_REQUIRED, reset to 1 on a fresh transition — and a leader-only
sweep task marks the account EXPIRED once the count reaches a configurable
threshold.  EXPIRED leaves the refresh pool, so dead accounts stop consuming
refresh cycles and surface in the admin list instead of lingering forever.
"""

from typing import TYPE_CHECKING

from app.platform.logging.logger import logger
from app.platform.runtime.clock import now_ms

from .commands import AccountPatch
from .enums import AccountStatus

if TYPE_CHECKING:
    from .models import AccountRecord
    from .repository import AccountRepository

REAUTH_FAIL_COUNT_KEY = "reauth_fail_count"
REAUTH_STUCK_REASON = "reauth_stuck"


async def bump_reauth_fail_count(
    repo: "AccountRepository", record: "AccountRecord"
) -> int:
    """Increment the consecutive reauth-failure counter for *record*.

    A fresh transition (account not currently REAUTH_REQUIRED) resets to 1, so
    the counter only grows across *consecutive* failed refresh attempts while
    the account stays REAUTH_REQUIRED.
    """
    ext = record.ext or {}
    count = (
        1
        if record.status != AccountStatus.REAUTH_REQUIRED
        else int(ext.get(REAUTH_FAIL_COUNT_KEY, 0)) + 1
    )
    await repo.patch_accounts(
        [
            AccountPatch(
                token=record.token,
                ext_merge={**ext, REAUTH_FAIL_COUNT_KEY: count},
            )
        ]
    )
    return count


async def recover_stuck_reauth_accounts(
    repo: "AccountRepository", *, threshold: int = 3
) -> int:
    """Mark REAUTH_REQUIRED accounts EXPIRED once consecutive failures pass *threshold*.

    Only accounts with ``ext.reauth_fail_count >= threshold`` are touched;
    healthy REAUTH accounts (fresh reauth, low counter) are left alone.
    Returns the number of accounts marked EXPIRED.
    """
    snapshot = await repo.runtime_snapshot()
    now = now_ms()
    count = 0
    for record in snapshot.items:
        if record.is_deleted() or record.status != AccountStatus.REAUTH_REQUIRED:
            continue
        ext = record.ext or {}
        fail_count = int(ext.get(REAUTH_FAIL_COUNT_KEY, 0))
        if fail_count < threshold:
            continue
        await repo.patch_accounts(
            [
                AccountPatch(
                    token=record.token,
                    status=AccountStatus.EXPIRED,
                    state_reason=REAUTH_STUCK_REASON,
                    ext_merge={
                        **ext,
                        "expired_at": now,
                        "expired_reason": REAUTH_STUCK_REASON,
                    },
                )
            ]
        )
        count += 1
        logger.info(
            "stuck reauth account expired: token={}... fail_count={} threshold={}",
            record.token[:10],
            fail_count,
            threshold,
        )
    return count


__all__ = [
    "REAUTH_FAIL_COUNT_KEY",
    "REAUTH_STUCK_REASON",
    "bump_reauth_fail_count",
    "recover_stuck_reauth_accounts",
]
