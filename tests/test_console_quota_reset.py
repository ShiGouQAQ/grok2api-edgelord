"""Console quota window reset must not clobber REAL (upstream-synced) windows.

Regression for the 30s console-quota-reset × 60s console-quota-recovery race
(review-B C1): ``reset_expired_console_windows`` runs every 30 s and its
WHERE condition 2 (``reset_at IS NOT NULL AND reset_at < now``) matches the
predicted recovery window (remaining == 0, reset_at == fetch + 24 h,
source == REAL). When that reset_at expires the reset task wins before the
60 s recovery task probes, overwriting the real window with the local
simulation (``{remaining: 20, reset_at: None, source: 0}``) — the recovery
task then sees remaining > 0 and skips, starving the real /v1/usage probe.

Fix: reset skips windows explicitly marked ``source == REAL``; local
simulation windows (DEFAULT / ESTIMATED / legacy rows without a source
field) are reset as before. The 60 s recovery task is the sole owner of
REAL windows.
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.control.account.backends.local import LocalAccountRepository
from app.control.account.commands import AccountPatch, AccountUpsert
from app.control.account.enums import QuotaSource
from app.control.account.models import QuotaWindow

TOKEN = "reset-race-tok"
NOW = 1_700_000_000_000


async def _make_repo(tmp_path, *, source, remaining, reset_at):
    repo = LocalAccountRepository(tmp_path / "accounts.db")
    await repo.initialize()
    await repo.upsert_accounts(
        [AccountUpsert(token=TOKEN, pool="basic", provider="grok_console")]
    )
    await repo.patch_accounts(
        [
            AccountPatch(
                token=TOKEN,
                quota_console=QuotaWindow(
                    remaining=remaining,
                    total=20,
                    window_seconds=86_400,
                    reset_at=reset_at,
                    synced_at=NOW - 60_000,
                    source=source,
                ).to_dict(),
            )
        ]
    )
    return repo


async def _window(repo) -> QuotaWindow | None:
    record = (await repo.get_accounts([TOKEN]))[0]
    return record.quota_set().get(5)


@patch("app.control.account.backends.local.now_ms", return_value=NOW)
class TestResetExcludesRealWindows:
    @pytest.mark.asyncio
    async def test_real_exhausted_past_reset_at_not_reset(self, tmp_path):
        """REAL predicted window past due is left for the recovery task, not reset."""
        repo = await _make_repo(
            tmp_path, source=QuotaSource.REAL, remaining=0, reset_at=NOW - 1_000
        )

        count = await repo.reset_expired_console_windows()

        assert count == 0
        w = await _window(repo)
        assert w is not None
        assert w.source == QuotaSource.REAL
        assert w.remaining == 0  # untouched

    @pytest.mark.asyncio
    async def test_real_positive_remaining_past_reset_at_not_reset(self, tmp_path):
        """REAL window with remaining > 0 and stale reset_at is not clobbered."""
        repo = await _make_repo(
            tmp_path, source=QuotaSource.REAL, remaining=12, reset_at=NOW - 1_000
        )

        count = await repo.reset_expired_console_windows()

        assert count == 0
        w = await _window(repo)
        assert w is not None
        assert w.remaining == 12

    @pytest.mark.asyncio
    async def test_default_exhausted_reset_as_before(self, tmp_path):
        """Local DEFAULT simulation window is still reset to the default quota."""
        repo = await _make_repo(
            tmp_path, source=QuotaSource.DEFAULT, remaining=0, reset_at=NOW - 1_000
        )

        count = await repo.reset_expired_console_windows()

        assert count == 1
        w = await _window(repo)
        assert w is not None
        assert w.remaining == 20
        assert w.reset_at is None
        assert w.source == QuotaSource.DEFAULT

    @pytest.mark.asyncio
    async def test_estimated_exhausted_reset_as_before(self, tmp_path):
        """Local ESTIMATED simulation window is still reset."""
        repo = await _make_repo(
            tmp_path, source=QuotaSource.ESTIMATED, remaining=0, reset_at=NOW - 1_000
        )

        count = await repo.reset_expired_console_windows()

        assert count == 1
        w = await _window(repo)
        assert w is not None
        assert w.remaining == 20

    @pytest.mark.asyncio
    async def test_legacy_missing_source_reset_as_before(self, tmp_path):
        """Rows without a source field (legacy) are treated as DEFAULT and reset."""
        repo = LocalAccountRepository(tmp_path / "accounts.db")
        await repo.initialize()
        await repo.upsert_accounts(
            [AccountUpsert(token=TOKEN, pool="basic", provider="grok_console")]
        )
        # Hand-write a legacy quota_console dict WITHOUT the source key.
        await repo.patch_accounts(
            [
                AccountPatch(
                    token=TOKEN,
                    quota_console={
                        "remaining": 0,
                        "total": 20,
                        "window_seconds": 3_600,
                        "reset_at": NOW - 1_000,
                        "synced_at": NOW - 60_000,
                    },
                )
            ]
        )

        count = await repo.reset_expired_console_windows()

        assert count == 1
        w = await _window(repo)
        assert w is not None
        assert w.remaining == 20
