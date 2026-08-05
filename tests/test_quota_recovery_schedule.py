"""Console quota recovery — next-probe scheduling math (PR #853 port).

Covers the pure scheduling semantics of ``probe_console_quota``:
* success (quota present OR zero chat window) → next probe = now + 24 h
  (Go ``consoleProbeInterval`` fixed window)
* transient/transport failure → bounded exponential backoff (base 1 s, max 1 min)
* 429 → same backoff path (never credential death)
* non-console accounts skipped
* 30 s min-interval guard (Go ``consoleMinInterval``)
"""

import pytest
from unittest.mock import AsyncMock

from app.control.account import quota_recovery as qr
from app.control.account.enums import QuotaSource
from app.control.account.models import AccountRecord, QuotaWindow
from app.dataplane.reverse.protocol.xai_console_usage import ConsoleUsageResult
from app.platform.errors import UpstreamError

TOKEN = "console-recovery-sched-tok"
NOW = 1_700_000_000_000


@pytest.fixture(autouse=True)
def _reset_state():
    qr._reset_state()
    yield
    qr._reset_state()


def _console_record(*, provider: str = "grok_console") -> AccountRecord:
    return AccountRecord(token=TOKEN, pool="basic", provider=provider)


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


class TestProbeNextDueMath:
    @pytest.mark.asyncio
    async def test_success_quota_present_fixed_24h_window(self, monkeypatch):
        _patch_fetch(
            monkeypatch, AsyncMock(return_value=_usage_result(chat_remaining=5))
        )

        result = await qr.probe_console_quota(_console_record(), now_ms=NOW)

        assert result is not None
        assert result.next_due_ms == NOW + qr.CONSOLE_PROBE_INTERVAL_MS
        assert result.window is not None
        assert result.window.remaining == 5

    @pytest.mark.asyncio
    async def test_success_zero_chat_fixed_24h_window(self, monkeypatch):
        _patch_fetch(
            monkeypatch, AsyncMock(return_value=_usage_result(chat_remaining=0))
        )

        result = await qr.probe_console_quota(_console_record(), now_ms=NOW)

        assert result is not None
        assert result.next_due_ms == NOW + qr.CONSOLE_PROBE_INTERVAL_MS
        assert result.window is not None
        assert result.window.remaining == 0
        assert result.window.reset_at == NOW + qr.CONSOLE_PROBE_INTERVAL_MS

    @pytest.mark.asyncio
    async def test_transient_failure_bounded_exponential_backoff(self, monkeypatch):
        _patch_fetch(
            monkeypatch,
            AsyncMock(side_effect=UpstreamError("transport failure", status=502)),
        )
        record = _console_record()

        first = await qr.probe_console_quota(record, now_ms=NOW)
        assert first is not None and first.window is None
        assert first.next_due_ms == NOW + qr.BACKOFF_BASE_MS

        # 60 s later (past the 30 s min-interval guard) — attempt 2.
        second = await qr.probe_console_quota(record, now_ms=NOW + 60_000)
        assert second is not None
        assert second.next_due_ms == NOW + 60_000 + 2 * qr.BACKOFF_BASE_MS

        # Attempt 3 → 4 s.
        third = await qr.probe_console_quota(record, now_ms=NOW + 120_000)
        assert third is not None
        assert third.next_due_ms == NOW + 120_000 + 4 * qr.BACKOFF_BASE_MS

    @pytest.mark.asyncio
    async def test_429_routes_to_backoff_not_credential_kill(self, monkeypatch):
        _patch_fetch(
            monkeypatch,
            AsyncMock(side_effect=UpstreamError("rate limited", status=429)),
        )

        result = await qr.probe_console_quota(_console_record(), now_ms=NOW)

        # 429 = RATE_LIMITED: bounded backoff, no window write, no credential death.
        assert result is not None
        assert result.window is None
        assert result.next_due_ms == NOW + qr.BACKOFF_BASE_MS

    @pytest.mark.asyncio
    async def test_attempts_reset_after_success(self, monkeypatch):
        fetch = AsyncMock(
            side_effect=[
                UpstreamError("transport failure", status=502),
                UpstreamError("transport failure", status=502),
                _usage_result(chat_remaining=5),
                UpstreamError("transport failure", status=502),
            ]
        )
        _patch_fetch(monkeypatch, fetch)
        record = _console_record()

        f1 = await qr.probe_console_quota(record, now_ms=NOW)
        f2 = await qr.probe_console_quota(record, now_ms=NOW + 60_000)
        assert f1 is not None and f2 is not None
        assert f1.next_due_ms == NOW + 1_000
        assert f2.next_due_ms == NOW + 60_000 + 2_000

        # Success resets the attempt counter — the next failure backs off from 1 s.
        s = await qr.probe_console_quota(record, now_ms=NOW + 120_000)
        assert s is not None
        assert s.window is not None and s.window.remaining == 5
        f3 = await qr.probe_console_quota(record, now_ms=NOW + 180_000)
        assert f3 is not None
        assert f3.next_due_ms == NOW + 180_000 + 1_000

    def test_backoff_capped_at_max(self):
        assert qr._backoff_ms(1) == 1_000
        assert qr._backoff_ms(2) == 2_000
        assert qr._backoff_ms(3) == 4_000
        assert qr._backoff_ms(4) == 8_000
        assert qr._backoff_ms(5) == 16_000
        assert qr._backoff_ms(6) == 32_000
        assert qr._backoff_ms(7) == 60_000
        assert qr._backoff_ms(10) == 60_000


class TestConsoleFilter:
    @pytest.mark.asyncio
    async def test_non_console_account_skipped(self, monkeypatch):
        fetch = AsyncMock(return_value=_usage_result(chat_remaining=5))
        _patch_fetch(monkeypatch, fetch)

        result = await qr.probe_console_quota(
            _console_record(provider="grok_web"), now_ms=NOW
        )

        assert result is None
        fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_min_interval_guard_blocks_early_reprobe(self, monkeypatch):
        _patch_fetch(
            monkeypatch, AsyncMock(return_value=_usage_result(chat_remaining=5))
        )
        record = _console_record()

        first = await qr.probe_console_quota(record, now_ms=NOW)
        assert first is not None

        # 10 s later — inside the 30 s min-interval guard → skipped.
        assert await qr.probe_console_quota(record, now_ms=NOW + 10_000) is None

        # Exactly 30 s later → allowed.
        allowed = await qr.probe_console_quota(record, now_ms=NOW + 30_000)
        assert allowed is not None
        assert allowed.next_due_ms == NOW + 30_000 + qr.CONSOLE_PROBE_INTERVAL_MS
