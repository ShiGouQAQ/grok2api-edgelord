"""Quota recovery ClaimToken concurrency protection (PR #853 port).

Port of Go ``memory/quota_queue.go``: while a recovery probe for
``(account_token, mode)`` is in flight, concurrent refresh attempts must NOT
clear the claim — the in-flight worker's result is authoritative. In-memory
singleflight (this repo's recovery runs single-leader; Go uses Redis only
because it is multi-node).
"""

import asyncio

import pytest
from unittest.mock import AsyncMock

from app.control.account import quota_recovery as qr
from app.control.account.enums import QuotaSource
from app.control.account.models import AccountRecord, QuotaWindow
from app.dataplane.reverse.protocol.xai_console_usage import ConsoleUsageResult
from app.platform.errors import UpstreamError

TOKEN = "console-recovery-claim-tok"
NOW = 1_700_000_000_000


@pytest.fixture(autouse=True)
def _reset_state():
    qr._reset_state()
    yield
    qr._reset_state()


def _console_record() -> AccountRecord:
    return AccountRecord(token=TOKEN, pool="basic", provider="grok_console")


def _usage_result(*, chat_remaining: int = 5) -> ConsoleUsageResult:
    chat = QuotaWindow(
        remaining=chat_remaining,
        total=20,
        window_seconds=86_400,
        reset_at=None,
        synced_at=NOW,
        source=QuotaSource.REAL,
    )
    display = QuotaWindow(
        remaining=10,
        total=10,
        window_seconds=0,
        reset_at=None,
        synced_at=NOW,
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


class TestClaimProtection:
    @pytest.mark.asyncio
    async def test_schedule_does_not_clear_in_flight_claim(self, monkeypatch):
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_fetch(token: str) -> ConsoleUsageResult:
            started.set()
            await release.wait()
            return _usage_result()

        _patch_fetch(monkeypatch, slow_fetch)

        # Pre-existing scheduled probe (Go queue entry).
        key = (TOKEN, qr.MODE_CONSOLE)
        assert qr.schedule_quota_recovery(TOKEN, qr.MODE_CONSOLE, 111) is True
        assert qr._next_probe_at[key] == 111

        probe_task = asyncio.create_task(
            qr.probe_console_quota(_console_record(), now_ms=NOW)
        )
        await started.wait()
        assert key in qr._claims  # claim registered while in flight

        # Concurrent refresh schedule attempt must NOT clear/overwrite the claim.
        assert qr.schedule_quota_recovery(TOKEN, qr.MODE_CONSOLE, 222) is False
        assert qr._next_probe_at[key] == 111  # untouched

        # CancelQuotaRecovery on a claimed event is a no-op (Go semantics).
        assert qr.cancel_quota_recovery(TOKEN, qr.MODE_CONSOLE) is False
        assert qr._next_probe_at[key] == 111

        release.set()
        result = await probe_task

        # Claim released after the probe completes.
        assert key not in qr._claims
        assert result is not None and result.window is not None
        assert qr.schedule_quota_recovery(TOKEN, qr.MODE_CONSOLE, 333) is True
        assert qr._next_probe_at[key] == 333

    @pytest.mark.asyncio
    async def test_second_probe_singleflights_on_shared_future(self, monkeypatch):
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def slow_fetch(token: str) -> ConsoleUsageResult:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return _usage_result()

        _patch_fetch(monkeypatch, slow_fetch)

        first = asyncio.create_task(
            qr.probe_console_quota(_console_record(), now_ms=NOW)
        )
        await started.wait()

        # Second concurrent probe must await the shared in-flight future,
        # not start a second fetch.
        second = asyncio.create_task(
            qr.probe_console_quota(_console_record(), now_ms=NOW)
        )
        await asyncio.sleep(0.01)
        assert calls == 1

        release.set()
        r1, r2 = await asyncio.gather(first, second)

        assert r1 is r2  # same authoritative result object
        assert r1 is not None and r1.window is not None
        assert calls == 1

    @pytest.mark.asyncio
    async def test_claim_released_after_failure(self, monkeypatch):
        _patch_fetch(
            monkeypatch,
            AsyncMock(side_effect=UpstreamError("transport failure", status=502)),
        )
        key = (TOKEN, qr.MODE_CONSOLE)

        result = await qr.probe_console_quota(_console_record(), now_ms=NOW)

        assert result is not None and result.window is None
        assert key not in qr._claims
        # The probe is pure — the leader task schedules the backoff afterwards.
        assert (
            qr.schedule_quota_recovery(TOKEN, qr.MODE_CONSOLE, result.next_due_ms)
            is True
        )
        assert qr._next_probe_at[key] == NOW + qr.BACKOFF_BASE_MS
        # And the account can be cancelled after release.
        assert qr.cancel_quota_recovery(TOKEN, qr.MODE_CONSOLE) is True
        assert key not in qr._next_probe_at

    @pytest.mark.asyncio
    async def test_cancel_removes_unclaimed_schedule(self):
        key = (TOKEN, qr.MODE_CONSOLE)
        assert qr.schedule_quota_recovery(TOKEN, qr.MODE_CONSOLE, 123) is True
        assert qr.cancel_quota_recovery(TOKEN, qr.MODE_CONSOLE) is True
        assert key not in qr._next_probe_at
        # Cancel of an absent event is a no-op returning False.
        assert qr.cancel_quota_recovery(TOKEN, qr.MODE_CONSOLE) is False
