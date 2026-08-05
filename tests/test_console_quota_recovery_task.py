"""Console quota recovery leader task (PR #853 port).

``recover_due_console_quotas`` scans the account directory for console
accounts whose predicted recovery window is past due (remaining == 0 and
reset_at IS NOT NULL and reset_at <= now — Go ``ListDueQuotaWindows``),
probes the real quota, and refreshes the stored window.
All network is mocked — the probe's fetch is patched at its module site.
"""

import pytest
from unittest.mock import AsyncMock

from app.control.account import quota_recovery as qr
from app.control.account.backends.local import LocalAccountRepository
from app.control.account.commands import AccountPatch, AccountUpsert
from app.control.account.enums import AccountStatus, QuotaSource
from app.control.account.models import QuotaWindow
from app.dataplane.reverse.protocol.xai_console_usage import ConsoleUsageResult
from app.platform.errors import UpstreamError

TOKEN = "console-recovery-task-tok"
NOW = 1_700_000_000_000


@pytest.fixture(autouse=True)
def _reset_state():
    qr._reset_state()
    yield
    qr._reset_state()


def _usage_result(*, chat_remaining: int, now: int = NOW) -> ConsoleUsageResult:
    chat = QuotaWindow(
        remaining=chat_remaining,
        total=20,
        window_seconds=86_400,
        reset_at=(now + 86_400_000) if chat_remaining == 0 else None,
        synced_at=now,
        source=QuotaSource.REAL,
    )
    display = QuotaWindow(
        remaining=10,
        total=10,
        window_seconds=0,
        reset_at=None,
        synced_at=now,
        source=QuotaSource.REAL,
    )
    return ConsoleUsageResult(
        chat=chat,
        image=display,
        video=display,
        used={"chat": 0, "image": 0, "video": 0},
    )


def _patch_fetch(monkeypatch, fn) -> None:
    monkeypatch.setattr(
        "app.dataplane.reverse.protocol.xai_console_usage.fetch_console_usage", fn
    )


async def _make_repo(
    tmp_path, *, provider: str, remaining: int, reset_at: int | None
) -> LocalAccountRepository:
    repo = LocalAccountRepository(tmp_path / "accounts.db")
    await repo.initialize()
    await repo.upsert_accounts(
        [AccountUpsert(token=TOKEN, pool="basic", provider=provider)]
    )
    await repo.patch_accounts(
        [
            AccountPatch(
                token=TOKEN,
                quota_console=QuotaWindow(
                    remaining=remaining,
                    total=20,
                    window_seconds=3_600,
                    reset_at=reset_at,
                    synced_at=NOW - 60_000,
                    source=QuotaSource.ESTIMATED,
                ).to_dict(),
            )
        ]
    )
    return repo


async def _fetch_record(repo) -> QuotaWindow | None:
    record = (await repo.get_accounts([TOKEN]))[0]
    return record.quota_set().get(5)


class TestRecoverDueConsoleQuotas:
    @pytest.mark.asyncio
    async def test_past_due_exhausted_account_probed_and_window_refreshed(
        self, tmp_path, monkeypatch
    ):
        _patch_fetch(
            monkeypatch, AsyncMock(return_value=_usage_result(chat_remaining=20))
        )
        repo = await _make_repo(
            tmp_path, provider="grok_console", remaining=0, reset_at=NOW - 1_000
        )

        count = await qr.recover_due_console_quotas(repo, now_ms=NOW)

        assert count == 1
        window = await _fetch_record(repo)
        assert window is not None
        assert window.remaining == 20
        assert window.reset_at is None  # healthy → no predicted window
        assert window.source == QuotaSource.REAL
        # Healthy account: Go AckQuotaRecovery — no further probe scheduled.
        assert (TOKEN, qr.MODE_CONSOLE) not in qr._next_probe_at

    @pytest.mark.asyncio
    async def test_still_exhausted_after_probe_schedules_24h(
        self, tmp_path, monkeypatch
    ):
        _patch_fetch(
            monkeypatch, AsyncMock(return_value=_usage_result(chat_remaining=0))
        )
        repo = await _make_repo(
            tmp_path, provider="grok_console", remaining=0, reset_at=NOW - 1_000
        )

        count = await qr.recover_due_console_quotas(repo, now_ms=NOW)

        assert count == 1
        window = await _fetch_record(repo)
        assert window is not None
        assert window.remaining == 0
        assert window.reset_at == NOW + qr.CONSOLE_PROBE_INTERVAL_MS
        # Event rescheduled at the 24 h predicted-recovery cadence.
        assert (
            qr._next_probe_at[(TOKEN, qr.MODE_CONSOLE)]
            == NOW + qr.CONSOLE_PROBE_INTERVAL_MS
        )
        # Not due again immediately.
        assert await qr.recover_due_console_quotas(repo, now_ms=NOW) == 0

    @pytest.mark.asyncio
    async def test_transient_failure_backs_off_and_skips_next_scan(
        self, tmp_path, monkeypatch
    ):
        fetch = AsyncMock(side_effect=UpstreamError("transport failure", status=502))
        _patch_fetch(monkeypatch, fetch)
        repo = await _make_repo(
            tmp_path, provider="grok_console", remaining=0, reset_at=NOW - 1_000
        )

        count = await qr.recover_due_console_quotas(repo, now_ms=NOW)

        assert count == 1
        # Backoff scheduled; window untouched (failure → no write).
        assert qr._next_probe_at[(TOKEN, qr.MODE_CONSOLE)] == NOW + qr.BACKOFF_BASE_MS
        window = await _fetch_record(repo)
        assert window is not None and window.remaining == 0

        # Second scan inside the backoff window (deadline NOW + 30 s) → skipped.
        assert await qr.recover_due_console_quotas(repo, now_ms=NOW + 500) == 0
        fetch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_due_window_skipped(self, tmp_path, monkeypatch):
        fetch = AsyncMock(return_value=_usage_result(chat_remaining=20))
        _patch_fetch(monkeypatch, fetch)
        repo = await _make_repo(
            tmp_path, provider="grok_console", remaining=0, reset_at=NOW + 5_000
        )

        assert await qr.recover_due_console_quotas(repo, now_ms=NOW) == 0
        fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_healthy_console_account_skipped(self, tmp_path, monkeypatch):
        fetch = AsyncMock(return_value=_usage_result(chat_remaining=20))
        _patch_fetch(monkeypatch, fetch)
        repo = await _make_repo(
            tmp_path, provider="grok_console", remaining=12, reset_at=NOW + 5_000
        )

        assert await qr.recover_due_console_quotas(repo, now_ms=NOW) == 0
        fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_console_account_skipped(self, tmp_path, monkeypatch):
        fetch = AsyncMock(return_value=_usage_result(chat_remaining=20))
        _patch_fetch(monkeypatch, fetch)
        repo = await _make_repo(
            tmp_path, provider="grok_web", remaining=0, reset_at=NOW - 1_000
        )

        assert await qr.recover_due_console_quotas(repo, now_ms=NOW) == 0
        fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_deleted_account_skipped(self, tmp_path, monkeypatch):
        fetch = AsyncMock(return_value=_usage_result(chat_remaining=20))
        _patch_fetch(monkeypatch, fetch)
        repo = await _make_repo(
            tmp_path, provider="grok_console", remaining=0, reset_at=NOW - 1_000
        )
        await repo.delete_accounts([TOKEN])

        assert await qr.recover_due_console_quotas(repo, now_ms=NOW) == 0
        fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_window_without_reset_at_skipped(self, tmp_path, monkeypatch):
        # Go ListDueQuotaWindows requires reset_at IS NOT NULL (377710f4) — a
        # null-reset exhausted window is never due, so it must not be probed.
        fetch = AsyncMock(return_value=_usage_result(chat_remaining=7))
        _patch_fetch(monkeypatch, fetch)
        repo = await _make_repo(
            tmp_path, provider="grok_console", remaining=0, reset_at=None
        )

        assert await qr.recover_due_console_quotas(repo, now_ms=NOW) == 0
        fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_429_probe_keeps_account_and_schedules_backoff(
        self, tmp_path, monkeypatch
    ):
        _patch_fetch(
            monkeypatch,
            AsyncMock(side_effect=UpstreamError("rate limited", status=429)),
        )
        repo = await _make_repo(
            tmp_path, provider="grok_console", remaining=0, reset_at=NOW - 1_000
        )

        count = await qr.recover_due_console_quotas(repo, now_ms=NOW)

        assert count == 1
        record = (await repo.get_accounts([TOKEN]))[0]
        assert record.status == AccountStatus.ACTIVE  # not credential-killed
        assert qr._next_probe_at[(TOKEN, qr.MODE_CONSOLE)] == NOW + qr.BACKOFF_BASE_MS
