"""Console quota predicted-recovery scheduling (Go→Python port of PR #853).

Ports ``backend/internal/application/quotarecovery/service.go`` and
``backend/internal/infra/runtime/memory/quota_queue.go``: a leader-only task
probes console accounts whose predicted recovery window is past due
(remaining == 0, reset_at <= now), refreshes the real chat quota, and
reschedules on the 24 h fixed cadence with bounded exponential backoff on
transport failure.

Deviation from Go: Go keeps the recovery queue in Redis for multi-node
coordination; this repo's recovery tasks are single-leader (advisory file
lock in ``app/main.py``), so the queue + ClaimToken live in memory.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.platform.logging.logger import logger
from app.platform.runtime.clock import now_ms as _clock_now_ms
from app.platform.errors import UpstreamError

from .commands import AccountPatch
from .models import AccountRecord, QuotaWindow

if TYPE_CHECKING:
    from .repository import AccountRepository

# Go consoleProbeInterval — fixed predicted-recovery cadence for console.
CONSOLE_PROBE_INTERVAL_MS = 86_400_000
# Go consoleMinInterval — never probe an account more often than this,
# except via the bounded backoff path.
CONSOLE_MIN_INTERVAL_MS = 30_000
# Go recovery backoff bounds (config defaults 30 s base / 30 min max, floor 5 s).
BACKOFF_BASE_MS = 30_000
BACKOFF_MAX_MS = 1_800_000

MODE_CONSOLE = "console"

# ``(token, mode)`` → asyncio.Future carrying the in-flight probe result.
# Presence = the event is claimed (Go ClaimToken): concurrent refresh paths
# must not clear it — the in-flight worker's result is authoritative.
# ponytail: in-memory claim registry, single-leader only — add Redis-backed
# claims only if multi-worker recovery ever runs concurrently (the scheduler
# file lock in app/main.py already prevents that).
_claims: dict[tuple[str, str], asyncio.Future[ConsoleProbeResult]] = {}
# ``(token, mode)`` → earliest ms a probe may run again (Go queue DueAt).
_next_probe_at: dict[tuple[str, str], int] = {}
# ``(token, mode)`` → consecutive failed probes (Go redis attempts counter).
_attempts: dict[tuple[str, str], int] = {}
# token → last probe start ms (Go consoleMinInterval guard).
_last_probe_at: dict[str, int] = {}


@dataclass(frozen=True, slots=True)
class ConsoleProbeResult:
    """Outcome of one probe: when to probe again, plus the fresh chat window.

    ``window`` is None when the probe failed (transport / 429 / parse error).
    """

    next_due_ms: int
    window: QuotaWindow | None = None


def _is_console_account(record: AccountRecord) -> bool:
    return record.provider == "grok_console"


def _backoff_ms(attempt: int) -> int:
    """Go service.backoff: base doubled per attempt, capped at max."""
    value = BACKOFF_BASE_MS
    for _ in range(1, attempt):
        if value >= BACKOFF_MAX_MS:
            break
        value *= 2
    return min(value, BACKOFF_MAX_MS)


def _bump_attempts(key: tuple[str, str]) -> int:
    attempt = _attempts.get(key, 0) + 1
    _attempts[key] = attempt
    return attempt


async def _probe_once(record: AccountRecord, now: int) -> ConsoleProbeResult:
    from app.dataplane.reverse.protocol.xai_console_usage import fetch_console_usage

    key = (record.token, MODE_CONSOLE)
    try:
        result = await fetch_console_usage(record.token)
    except UpstreamError as exc:
        attempt = _bump_attempts(key)
        if exc.status == 429:
            logger.info(
                "console quota probe rate limited: token={}... attempt={}",
                record.token[:10],
                attempt,
            )
        else:
            logger.debug(
                "console quota probe upstream failure: token={}... status={} attempt={}",
                record.token[:10],
                exc.status,
                attempt,
            )
        return ConsoleProbeResult(now + _backoff_ms(attempt))
    except Exception as exc:
        attempt = _bump_attempts(key)
        logger.debug(
            "console quota probe failed: token={}... error={} attempt={}",
            record.token[:10],
            exc,
            attempt,
        )
        return ConsoleProbeResult(now + _backoff_ms(attempt))
    _attempts.pop(key, None)
    return ConsoleProbeResult(now + CONSOLE_PROBE_INTERVAL_MS, result.chat)


async def probe_console_quota(
    record: AccountRecord, *, now_ms: int | None = None
) -> ConsoleProbeResult | None:
    """Probe one console account's real quota; return the next probe schedule.

    Singleflight per ``(token, mode)``: a second concurrent probe awaits the
    in-flight worker's shared future instead of firing a second fetch.
    Returns None when skipped (non-console account / 30 s min-interval guard).
    """
    if not _is_console_account(record):
        return None
    now = now_ms if now_ms is not None else _clock_now_ms()
    key = (record.token, MODE_CONSOLE)

    in_flight = _claims.get(key)
    if in_flight is not None:
        return await asyncio.shield(in_flight)

    if now - _last_probe_at.get(record.token, 0) < CONSOLE_MIN_INTERVAL_MS:
        return None

    future: asyncio.Future[ConsoleProbeResult] = (
        asyncio.get_running_loop().create_future()
    )
    _claims[key] = future
    _last_probe_at[record.token] = now
    try:
        result = await _probe_once(record, now)
        future.set_result(result)
        return result
    except BaseException as exc:
        future.set_exception(exc)
        raise
    finally:
        _claims.pop(key, None)


def schedule_quota_recovery(token: str, mode: str, due_at_ms: int) -> bool:
    """Register/update the next probe time for ``(token, mode)``.

    Returns False when the event is claimed (Go ScheduleQuotaRecovery): a
    concurrent refresh must NOT clear the in-flight worker's claim.
    """
    key = (token, mode)
    if key in _claims:
        return False
    _next_probe_at[key] = due_at_ms
    return True


def cancel_quota_recovery(token: str, mode: str) -> bool:
    """Remove the scheduled probe for ``(token, mode)`` (Go CancelQuotaRecovery).

    Returns False when the event is claimed — the worker's probe is
    authoritative and must not be cancelled.
    """
    key = (token, mode)
    if key in _claims:
        return False
    removed = _next_probe_at.pop(key, None) is not None
    _attempts.pop(key, None)
    _last_probe_at.pop(token, None)
    return removed


async def recover_due_console_quotas(
    repo: "AccountRepository", *, now_ms: int | None = None
) -> int:
    """Leader task: probe console accounts whose predicted recovery window is due.

    A console account is due when its chat quota is exhausted (remaining == 0)
    and its reset window is past due (reset_at IS NOT NULL and reset_at <= now,
    matching Go ListDueQuotaWindows).
    Probes sequentially; on success the fresh chat window is persisted (the
    account returns to active routing), on failure the bounded backoff guard
    suppresses re-probing until it elapses.
    Returns the number of accounts probed.
    """
    now = now_ms if now_ms is not None else _clock_now_ms()
    snapshot = await repo.runtime_snapshot()
    probed = 0
    for record in snapshot.items:
        if record.is_deleted() or not _is_console_account(record):
            continue
        chat = record.quota_set().get(5)
        if chat is None or not chat.is_exhausted():
            continue
        if chat.reset_at is None or chat.reset_at > now:
            continue
        key = (record.token, MODE_CONSOLE)
        if _next_probe_at.get(key, 0) > now:
            continue

        result = await probe_console_quota(record, now_ms=now)
        if result is None:
            continue
        probed += 1

        if result.window is not None:
            await repo.patch_accounts(
                [
                    AccountPatch(
                        token=record.token, quota_console=result.window.to_dict()
                    )
                ]
            )
            if result.window.remaining > 0:
                # Go AckQuotaRecovery: healthy again — no probe until the
                # window is next exhausted.
                _next_probe_at.pop(key, None)
                _attempts.pop(key, None)
                continue
        schedule_quota_recovery(record.token, MODE_CONSOLE, result.next_due_ms)
    return probed


def _reset_state() -> None:
    """Test hook: clear all in-memory scheduling state."""
    _claims.clear()
    _next_probe_at.clear()
    _attempts.clear()
    _last_probe_at.clear()


__all__ = [
    "BACKOFF_BASE_MS",
    "BACKOFF_MAX_MS",
    "CONSOLE_MIN_INTERVAL_MS",
    "CONSOLE_PROBE_INTERVAL_MS",
    "ConsoleProbeResult",
    "MODE_CONSOLE",
    "cancel_quota_recovery",
    "probe_console_quota",
    "recover_due_console_quotas",
    "schedule_quota_recovery",
]
