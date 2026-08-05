"""Account refresh service — mode-aware usage synchronisation."""

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.control.model.enums import ALL_MODES_FULL
from app.platform.config.snapshot import get_config
from app.platform.errors import UpstreamError
from app.platform.logging.logger import logger
from app.platform.runtime.batch import run_batch
from app.platform.runtime.clock import now_ms

from .enums import AccountStatus, QuotaSource
from .models import AccountRecord, QuotaWindow
from .quota_defaults import (
    _MODE_KEYS,
    default_quota_window,
    infer_pool,
    normalize_quota_window,
    supported_mode_ids,
    supports_mode,
)
from .state_machine import is_manageable

if TYPE_CHECKING:
    from .repository import AccountRepository


@dataclass
class RefreshResult:
    checked: int = 0
    refreshed: int = 0
    recovered: int = 0
    expired: int = 0
    disabled: int = 0
    rate_limited: int = 0
    failed: int = 0

    def merge(self, other: "RefreshResult") -> None:
        self.checked += other.checked
        self.refreshed += other.refreshed
        self.recovered += other.recovered
        self.expired += other.expired
        self.disabled += other.disabled
        self.rate_limited += other.rate_limited
        self.failed += other.failed


def _infer_pool_from_live_windows(windows: dict[int, QuotaWindow]) -> str | None:
    """Infer pool only from quota totals that identify an entitlement tier."""
    auto_win = windows.get(0)
    if auto_win is not None:
        inferred = infer_pool(windows)  # type: ignore[arg-type]
        if inferred != "basic" or auto_win.total == 20:
            return inferred

    for mode_id in (2, 3, 4):
        win = windows.get(mode_id)
        if win is None:
            continue
        if win.total == 150:
            return "heavy"
        if win.total == 50:
            return "super"
        if mode_id == 3 and win.total == 20:
            return "heavy"
    return None


# Go ClassifyCredentialRejection credit-exhaustion markers (b4c7baab): bodies
# carrying any of these are quota exhaustion, never credential rejection.
_CREDIT_EXHAUSTION_SIGNALS = (
    "run out of credits",
    "out of credits",
    "usage balance exhausted",
    "usage limit reached",
)


def _is_quota_exhaustion_error(exc: UpstreamError) -> bool:
    """Map Go ClassifyCredentialRejection quota signals onto errors.py flags.

    errors.py's ``from_http_response`` already sets quota_exhausted /
    free_quota_exhausted / model_quota_exhausted for 402/403/429; this bridge
    covers manually-constructed UpstreamErrors (e.g. fetch_build_billing)
    whose body carries a credit-exhaustion marker without classification.
    Matches Go exactly: 401 is always credential rejection, quota markers win
    over a generic credential_rejected flag on 403.
    """
    if exc.quota_exhausted or exc.free_quota_exhausted or exc.model_quota_exhausted:
        return True
    if exc.status == 401:
        return False
    text = f"{(exc.details or {}).get('body', '')} {exc}".lower()
    return any(signal in text for signal in _CREDIT_EXHAUSTION_SIGNALS)


class AccountRefreshService:
    """Fetches real quota data from the upstream usage API and persists it.

    Triggers:
      1. Import   — fetch all modes supported by the account's pool.
      2. Call     — fetch the called mode only (async, non-blocking).
      3. Schedule — refresh one pool per loop using that pool's supported modes.
    """

    def __init__(self, repository: "AccountRepository") -> None:
        self._repo = repository
        self._lock = asyncio.Lock()
        self._od_lock = asyncio.Lock()
        self._od_last = 0.0
        # Per-token locks serializing the quota read-modify-write in
        # refresh_call_async -> _apply_single_mode (concurrent decrements).
        self._token_locks: dict[str, asyncio.Lock] = {}

    def _token_lock(self, token: str) -> asyncio.Lock:
        """Return the per-token lock serializing this account's quota updates.

        Get-or-create is atomic under asyncio (no ``await`` between the read
        and the insert, so tasks cannot interleave), so concurrent callers
        always share one lock per token.
        # ponytail: locks live for the process lifetime, bounded by the number
        # of distinct tokens (account count); weakref + refcount if account
        # churn ever matters.
        """
        lock = self._token_locks.get(token)
        if lock is None:
            lock = asyncio.Lock()
            self._token_locks[token] = lock
        return lock

    # ------------------------------------------------------------------
    # Usage API fetch (delegates to dataplane reverse protocol)
    # ------------------------------------------------------------------

    async def _fetch_all_quotas(
        self, token: str, pool: str, *, bootstrap: bool = False
    ) -> dict[int, QuotaWindow] | None:
        """Fetch quota windows for every mode supported by *pool*.

        Examples:
          - basic -> fast
          - super -> auto / fast / expert / grok_4_3
          - heavy -> auto / fast / expert / heavy / grok_4_3
        """
        try:
            from app.dataplane.reverse.protocol.xai_usage import fetch_all_quotas

            mode_ids = supported_mode_ids(pool)
            if bootstrap:
                # Bootstrap refreshes need entitlement probes even when the
                # current local image is basic. If auto is flaky, expert/heavy
                # windows still provide enough signal to avoid a sticky
                # misclassification.
                mode_ids = tuple(dict.fromkeys((0, 2, 3, 4, *mode_ids)))
            return await fetch_all_quotas(token, mode_ids)
        except UpstreamError:
            raise
        except Exception as exc:
            logger.debug(
                "account quota fetch failed: token={}... pool={} error={}",
                token[:10],
                pool,
                exc,
            )
            return None

    async def _fetch_mode_quota(
        self, token: str, pool: str, mode_id: int
    ) -> QuotaWindow | None:
        """Fetch a single mode quota window."""
        if not supports_mode(pool, mode_id):
            logger.debug(
                "account mode quota fetch skipped: token={}... pool={} mode_id={} reason=unsupported_mode",
                token[:10],
                pool,
                mode_id,
            )
            return None
        try:
            from app.dataplane.reverse.protocol.xai_usage import fetch_mode_quota

            return await fetch_mode_quota(token, mode_id)
        except UpstreamError:
            raise
        except Exception as exc:
            logger.debug(
                "account mode quota fetch failed: token={}... pool={} mode_id={} error={}",
                token[:10],
                pool,
                mode_id,
                exc,
            )
            return None

    # ------------------------------------------------------------------
    # Core refresh logic
    # ------------------------------------------------------------------

    async def refresh_on_import(self, tokens: list[str]) -> RefreshResult:
        """Called after bulk import — sync real quotas for all accounts."""
        records = await self._repo.get_accounts(tokens)
        active = [r for r in records if is_manageable(r)]
        if not active:
            return RefreshResult(checked=len(records))

        concurrency = get_config("account.refresh.usage_concurrency", 15)
        results = await run_batch(
            active,
            lambda r: self._refresh_one(r, apply_fallback=True, bootstrap=True),
            concurrency=concurrency,
        )
        agg = RefreshResult(checked=len(records))
        for r in results:
            agg.merge(r)
        return agg

    async def refresh_call_async(self, token: str, mode_id: int) -> None:
        """Fire-and-forget single-mode quota sync after a successful call."""
        # Per-token lock: _apply_single_mode decrements `remaining` from the
        # record snapshot read below — without serialization two concurrent
        # calls both read 20 and both write 19 (lost update).
        async with self._token_lock(token):
            record = (await self._repo.get_accounts([token]) or [None])[0]
            if record is None or record.is_deleted():
                return

            # mode_id=5 (CONSOLE) 是本地管理的配额，不需要请求 xai usage API
            # 直接做本地扣减并更新 usage_use_count
            if mode_id == 5:
                await self._apply_single_mode(
                    record, mode_id, window=None, is_use=True, use_at_ms=now_ms()
                )
                return

            try:
                window = await self._fetch_mode_quota(token, record.pool, mode_id)
            except UpstreamError as exc:
                if await self._expire_invalid_credentials(record, exc):
                    return
                raise
            await self._apply_single_mode(
                record, mode_id, window, is_use=True, use_at_ms=now_ms()
            )

    async def refresh_scheduled(self, pool: str | None = None) -> RefreshResult:
        """Periodic refresh — fetch real quotas for all (or one pool's) accounts.

        Args:
            pool: When set, only refreshes accounts belonging to that pool.
                  When ``None``, refreshes all pools.
        """
        snapshot = await self._repo.runtime_snapshot()
        records = [r for r in snapshot.items if is_manageable(r)]
        if pool is not None:
            records = [r for r in records if r.pool == pool]

        concurrency = get_config("account.refresh.usage_concurrency", 15)
        results = await run_batch(
            records,
            lambda r: self._refresh_one(r, apply_fallback=True),
            concurrency=concurrency,
        )
        agg = RefreshResult()
        for r in results:
            agg.merge(r)
        return agg

    async def refresh_on_demand(self) -> RefreshResult:
        """Throttled on-demand refresh triggered by request path."""
        min_interval = float(
            get_config("account.refresh.on_demand_min_interval_sec", 300)
        )
        import time

        now = time.monotonic()
        if now - self._od_last < min_interval:
            return RefreshResult()
        if self._od_lock.locked():
            return RefreshResult()
        async with self._od_lock:
            now = time.monotonic()
            if now - self._od_last < min_interval:
                return RefreshResult()
            result = await self.refresh_scheduled()
            self._od_last = time.monotonic()
            return result

    async def refresh_tokens(self, tokens: list[str]) -> RefreshResult:
        """Explicit refresh for a list of tokens (admin / manual trigger)."""
        records = [r for r in await self._repo.get_accounts(tokens) if is_manageable(r)]
        concurrency = get_config("account.refresh.usage_concurrency", 15)
        results = await run_batch(
            records,
            lambda r: self._refresh_one(r, bootstrap=True),
            concurrency=concurrency,
        )
        agg = RefreshResult()
        for r in results:
            agg.merge(r)
        return agg

    # ------------------------------------------------------------------
    # Per-account refresh
    # ------------------------------------------------------------------

    async def _refresh_one(
        self,
        record: AccountRecord,
        *,
        apply_fallback: bool = False,
        bootstrap: bool = False,
    ) -> RefreshResult:
        """Fetch all pool-supported modes from the usage API and persist them.

        apply_fallback=True  — used by scheduled/import paths: when API fails,
                               decrement REAL quotas or reset expired DEFAULT windows.
        apply_fallback=False — used by manual/on-demand paths: if API fails, return
                               failed=1 immediately without touching stored data.
        """
        if record.is_deleted():
            return RefreshResult()

        # Build OAuth tokens are not sso cookies — rate-limits always 401s.
        # Probe the billing API instead (port of Go QuotaBilling).
        if record.pool == "build" or record.provider == "grok_build":
            return await self._refresh_build_billing(
                record, apply_fallback=apply_fallback
            )

        # Console SSO accounts refresh from console.x.ai /v1/usage (DPoP);
        # grok.com rate-limits rejects console tokens (401 → false reauth).
        if record.provider == "grok_console":
            return await self._refresh_console_usage(
                record, apply_fallback=apply_fallback
            )

        try:
            windows = await self._fetch_all_quotas(
                record.token, record.pool, bootstrap=bootstrap
            )
        except UpstreamError as exc:
            if await self._expire_invalid_credentials(record, exc):
                return RefreshResult(checked=1, expired=1, failed=0)
            raise

        # API call completely failed — no real data available.
        if windows is None:
            if not apply_fallback:
                return RefreshResult(checked=1, failed=1)
            # Scheduled/import path: apply conservative fallback.
            return await self._apply_fallback(record)

        # We got at least a response — apply real data per mode.
        qs = record.quota_set()
        now = now_ms()
        patches: dict[str, dict] = {}
        refreshed = False
        inferred = _infer_pool_from_live_windows(windows)

        # Web SSO tier re-probe: if inference suggests downgrade (None/basic)
        # but the account previously held a higher tier (super/heavy), try
        # fetching a higher-tier mode quota to confirm the real tier before
        # downgrading.  Ported from Go 4f34707 — SyncQuota re-probes before
        # defaulting to Basic.
        if (inferred is None or inferred == "basic") and record.pool in (
            "super",
            "heavy",
        ):
            probe_mode = 3 if record.pool == "heavy" else 2  # heavy=3, expert=2
            try:
                probe_window = await self._fetch_mode_quota(
                    record.token, record.pool, probe_mode
                )
                if probe_window is not None and probe_window.total > 0:
                    windows[probe_mode] = probe_window
                    inferred = _infer_pool_from_live_windows(windows)
            except UpstreamError:
                pass  # probe failed, keep original inference

        effective_pool = inferred if (bootstrap and inferred) else record.pool

        for mode in ALL_MODES_FULL:
            mode_id = int(mode)
            if mode_id in windows:
                window = normalize_quota_window(
                    effective_pool, mode_id, windows[mode_id]
                )
                if window is None:
                    continue
                patches[_MODE_KEYS[mode_id]] = window.to_dict()
                refreshed = True
            elif apply_fallback:
                existing = qs.get(mode_id)
                if existing is None:
                    continue
                if existing.source == QuotaSource.REAL:
                    patches[_MODE_KEYS[mode_id]] = QuotaWindow(
                        remaining=max(0, existing.remaining - 1),
                        total=existing.total,
                        window_seconds=existing.window_seconds,
                        reset_at=existing.reset_at,
                        synced_at=existing.synced_at,
                        source=QuotaSource.ESTIMATED,
                    ).to_dict()
                elif existing.is_window_expired(now):
                    default = default_quota_window(effective_pool, mode_id)
                    if default is None:
                        continue
                    patches[_MODE_KEYS[mode_id]] = QuotaWindow(
                        remaining=default.total,
                        total=default.total,
                        window_seconds=default.window_seconds,
                        reset_at=now + default.window_seconds * 1000,
                        synced_at=now,
                        source=QuotaSource.DEFAULT,
                    ).to_dict()

        if not patches:
            return RefreshResult(checked=1, failed=0 if refreshed else 1)

        # Infer pool type from live quota data and patch if it changed.
        pool_patch = (
            inferred if inferred is not None and inferred != record.pool else None
        )
        if pool_patch:
            logger.info(
                "account pool updated from live quota: token={}... previous_pool={} current_pool={}",
                record.token[:10],
                record.pool,
                inferred,
            )

        from .commands import AccountPatch

        # Reauth account recovers on refresh success — clear_failures is the
        # only patch flag that wipes state_reason (backends skip None fields).
        reauth_restore = refreshed and record.status == AccountStatus.REAUTH_REQUIRED

        await self._repo.patch_accounts(
            [
                AccountPatch(
                    token=record.token,
                    pool=pool_patch,
                    last_sync_at=now_ms() if refreshed else None,
                    usage_sync_delta=1 if refreshed else None,
                    clear_failures=reauth_restore,
                    **patches,  # type: ignore[arg-type]
                )
            ]
        )
        was_cooling = record.status == AccountStatus.COOLING
        return RefreshResult(
            checked=1,
            refreshed=1 if refreshed else 0,
            failed=0 if refreshed else 1,
            recovered=1 if (was_cooling and refreshed) else 0,
        )

    async def _refresh_build_billing(
        self,
        record: AccountRecord,
        *,
        apply_fallback: bool,
    ) -> RefreshResult:
        """Probe Build OAuth billing (api.x.ai/billing/usage) and persist it."""
        from dataclasses import asdict

        from app.dataplane.reverse.protocol.xai_billing import fetch_build_billing

        ext = record.ext or {}
        token = ext.get("build_access_token") or record.token

        try:
            billing = await fetch_build_billing(token)
        except UpstreamError as exc:
            # Go b4c7baab: quota exhaustion is not a credential death. Record
            # the per-account failed outcome (ForEachObserved-style: each
            # account's outcome captured independently, no batch abort) and
            # keep the account in the pool — never EXPIRED on quota bodies.
            if _is_quota_exhaustion_error(exc):
                return RefreshResult(checked=1, failed=1)
            if not getattr(exc, "credential_rejected", False):
                raise
            if not await self._expire_invalid_credentials(record, exc):
                # Structured flag is authoritative even when the body
                # heuristics miss — force the EXPIRED state directly.
                from .commands import AccountPatch

                ts = now_ms()
                await self._repo.patch_accounts(
                    [
                        AccountPatch(
                            token=record.token,
                            status=AccountStatus.EXPIRED,
                            last_fail_at=ts,
                            last_fail_reason="invalid_credentials",
                            state_reason="invalid_credentials",
                            ext_merge={
                                **ext,
                                "expired_at": ts,
                                "expired_reason": "invalid_credentials",
                            },
                        )
                    ]
                )
            return RefreshResult(checked=1, expired=1)
        except Exception:
            return RefreshResult(checked=1, failed=1)

        from .commands import AccountPatch

        await self._repo.patch_accounts(
            [
                AccountPatch(
                    token=record.token,
                    ext_merge={**ext, "build_billing": asdict(billing)},
                )
            ]
        )
        return RefreshResult(checked=1, refreshed=1)

    async def _refresh_console_usage(
        self,
        record: AccountRecord,
        *,
        apply_fallback: bool,
    ) -> RefreshResult:
        """Fetch the real console quota (chat/image/video) and persist it.

        Go PR #853: the real upstream quota SUPERSEDES the local simulator
        when the fetch succeeds (chat → quota_console; image/video → ext,
        display-only). Transient failures (clearance required, malformed
        payload, transport, 429) keep the account and fall back to the local
        quota path; credential rejection follows _expire_invalid_credentials.
        """
        from app.dataplane.reverse.protocol.xai_console_usage import (
            ConsoleClearanceRequiredError,
            fetch_console_usage,
        )

        from .quota_defaults import console_usage_windows

        try:
            result = await fetch_console_usage(record.token)
        except ConsoleClearanceRequiredError as exc:
            logger.warning(
                "console quota refresh clearance required: token={}... error={}",
                record.token[:10],
                exc,
            )
            return await self._console_refresh_fallback(record, apply_fallback)
        except UpstreamError as exc:
            if exc.status == 429:
                logger.warning(
                    "console quota refresh rate limited: token={}... error={}",
                    record.token[:10],
                    exc,
                )
                result = await self._console_refresh_fallback(record, apply_fallback)
                result.rate_limited = 1
                return result
            if getattr(exc, "credential_rejected", False):
                if await self._expire_invalid_credentials(record, exc):
                    return RefreshResult(checked=1, expired=1)
                return RefreshResult(checked=1, failed=1)
            logger.warning(
                "console quota refresh transient failure: token={}... error={}",
                record.token[:10],
                exc,
            )
            return await self._console_refresh_fallback(record, apply_fallback)
        except Exception as exc:
            logger.warning(
                "console quota refresh failed: token={}... error={}",
                record.token[:10],
                exc,
            )
            return await self._console_refresh_fallback(record, apply_fallback)

        windows = console_usage_windows(result)
        now = now_ms()

        from .commands import AccountPatch

        reauth_restore = record.status == AccountStatus.REAUTH_REQUIRED
        was_cooling = record.status == AccountStatus.COOLING
        await self._repo.patch_accounts(
            [
                AccountPatch(
                    token=record.token,
                    quota_console=windows.chat.to_dict(),
                    ext_merge={
                        **(record.ext or {}),
                        "console_quota_image": windows.image.to_dict(),
                        "console_quota_video": windows.video.to_dict(),
                    },
                    last_sync_at=now,
                    usage_sync_delta=1,
                    clear_failures=reauth_restore,
                )
            ]
        )
        return RefreshResult(
            checked=1,
            refreshed=1,
            recovered=1 if was_cooling else 0,
        )

    async def _console_refresh_fallback(
        self,
        record: AccountRecord,
        apply_fallback: bool,
    ) -> RefreshResult:
        """Keep the local simulator quota on a transient console-fetch failure."""
        if not apply_fallback:
            return RefreshResult(checked=1, failed=1)
        return await self._apply_fallback(record)

    async def _apply_fallback(self, record: AccountRecord) -> RefreshResult:
        """Conservative fallback when API is unreachable (scheduled/import path only)."""
        qs = record.quota_set()
        now = now_ms()
        patches: dict[str, dict] = {}

        for mode in ALL_MODES_FULL:
            mode_id = int(mode)
            existing = qs.get(mode_id)
            if existing is None:
                continue
            if existing.source == QuotaSource.REAL:
                patches[_MODE_KEYS[mode_id]] = QuotaWindow(
                    remaining=max(0, existing.remaining - 1),
                    total=existing.total,
                    window_seconds=existing.window_seconds,
                    reset_at=existing.reset_at,
                    synced_at=existing.synced_at,
                    source=QuotaSource.ESTIMATED,
                ).to_dict()
            elif existing.is_window_expired(now):
                default = default_quota_window(record.pool, mode_id)
                if default is None:
                    continue
                patches[_MODE_KEYS[mode_id]] = QuotaWindow(
                    remaining=default.total,
                    total=default.total,
                    window_seconds=default.window_seconds,
                    reset_at=now + default.window_seconds * 1000,
                    synced_at=now,
                    source=QuotaSource.DEFAULT,
                ).to_dict()

        if patches:
            from .commands import AccountPatch

            await self._repo.patch_accounts(
                [AccountPatch(token=record.token, **patches)]
            )  # type: ignore[arg-type]

        return RefreshResult(checked=1, failed=1)

    async def record_failure_async(
        self, token: str, mode_id: int, exc: BaseException | None = None
    ) -> None:
        """Fire-and-forget: persist failure counter and timestamp after a failed call."""
        from .commands import AccountPatch

        try:
            if exc is not None:
                record = next(iter(await self._repo.get_accounts([token])), None)
                if record is not None and await self._expire_invalid_credentials(
                    record, exc
                ):
                    return
                if (
                    record is not None
                    and getattr(exc, "status", None) == 429
                    and mode_id in _MODE_KEYS
                ):
                    now = now_ms()
                    quota_patch: dict[str, dict] = {}
                    window = record.quota_set().get(mode_id)
                    extra_patch: dict = {}
                    if window is not None:
                        if mode_id == 5:
                            # Console 429: 一次直接清零（扣 20），账号当前窗口不再可用
                            # 立即启动恢复计时器，窗口结束后由巡检任务重置
                            new_remaining = 0
                            reset_at = window.reset_at
                            if reset_at is None and window.window_seconds > 0:
                                reset_at = now + window.window_seconds * 1000
                            quota_patch[_MODE_KEYS[mode_id]] = QuotaWindow(
                                remaining=new_remaining,
                                total=window.total,
                                window_seconds=window.window_seconds,
                                reset_at=reset_at,
                                synced_at=window.synced_at,
                                source=QuotaSource.ESTIMATED,
                            ).to_dict()
                            # Console 专属 429 计数器（独立于 usage_fail_count，
                            # 避免被 500/网络超时等其他失败干扰）。
                            # 12 小时滑动窗口：距离上次 429 超过 12 小时 → 计数重置为 0
                            ext_data = record.ext or {}
                            last_429_at = int(ext_data.get("console_429_last_at", 0))
                            sliding_window_ms = 12 * 3600 * 1000
                            if (
                                last_429_at > 0
                                and (now - last_429_at) > sliding_window_ms
                            ):
                                console_429_count = 0
                            else:
                                console_429_count = int(
                                    ext_data.get("console_429_count", 0)
                                )
                            new_429_count = console_429_count + 1
                            ext_merge: dict = {
                                **ext_data,
                                "console_429_count": new_429_count,
                                "console_429_last_at": now,
                            }
                            # 12 小时内累计 3 次 429 标记为 EXPIRED 异常组
                            # (REAUTH_REQUIRED 账号保持 reauth——429 是配额问题，
                            # 不应覆盖并发 refresh 已标记的 reauth 语义)
                            if (
                                new_429_count >= 3
                                and record.status != AccountStatus.REAUTH_REQUIRED
                            ):
                                extra_patch["status"] = AccountStatus.EXPIRED
                                extra_patch["state_reason"] = (
                                    "console_429_threshold_exceeded"
                                )
                                ext_merge["expired_at"] = now
                                ext_merge["expired_reason"] = (
                                    "console_429_threshold_exceeded"
                                )
                                logger.info(
                                    "account marked expired due to repeated 429: token={}... count={}",
                                    token[:10],
                                    new_429_count,
                                )
                            extra_patch["ext_merge"] = ext_merge
                        else:
                            # 非 console 模式保持原有清零逻辑
                            reset_at = (
                                window.reset_at
                                if window.reset_at is not None and window.reset_at > now
                                else now + max(window.window_seconds, 1) * 1000
                            )
                            quota_patch[_MODE_KEYS[mode_id]] = QuotaWindow(
                                remaining=0,
                                total=window.total,
                                window_seconds=window.window_seconds,
                                reset_at=reset_at,
                                synced_at=window.synced_at,
                                source=QuotaSource.ESTIMATED,
                            ).to_dict()
                    await self._repo.patch_accounts(
                        [
                            AccountPatch(
                                token=token,
                                usage_fail_delta=1,
                                last_fail_at=now,
                                last_fail_reason="rate_limited",
                                **extra_patch,
                                **quota_patch,
                            )
                        ]
                    )
                    return
            await self._repo.patch_accounts(
                [
                    AccountPatch(
                        token=token,
                        usage_fail_delta=1,
                        last_fail_at=now_ms(),
                    )
                ]
            )
        except Exception as exc:
            logger.debug(
                "account failure record update failed: token={}... error={}",
                token[:10],
                exc,
            )

    async def _apply_single_mode(
        self,
        record: AccountRecord,
        mode_id: int,
        window: QuotaWindow | None,
        *,
        is_use: bool = False,
        use_at_ms: int | None = None,
    ) -> None:
        qs = record.quota_set()
        mode_key = _MODE_KEYS.get(mode_id)
        if mode_key is None:
            logger.warning(
                "account single-mode sync skipped: token={}... pool={} mode_id={} reason=unknown_mode",
                record.token[:10],
                record.pool,
                mode_id,
            )
            return

        quota_patch: dict[str, dict] = {}
        if window is not None:
            normalized = normalize_quota_window(record.pool, mode_id, window)
            if normalized is None:
                logger.debug(
                    "account single-mode quota patch skipped: token={}... pool={} mode_id={} reason=unsupported_mode",
                    record.token[:10],
                    record.pool,
                    mode_id,
                )
                return
            quota_patch[mode_key] = normalized.to_dict()
        else:
            existing = qs.get(mode_id)
            if existing is not None:
                now = now_ms()
                # 如果窗口已过期，重置为默认值（适用于本地管理的配额，如 console）
                if existing.is_window_expired(now):
                    default = default_quota_window(record.pool, mode_id)
                    if default is not None:
                        new_remaining = max(0, default.total - 1)  # 本次调用消耗1次
                        # console (mode_id=5) 阈值轮换策略：reset_at=None，
                        # 让后续扣减在 remaining<=12 时再启动计时器，与 else 分支一致；
                        # 非 console 模式保持原行为：首次使用即启动计时器
                        if mode_id == 5:
                            reset_at = None
                        else:
                            reset_at = now + default.window_seconds * 1000
                        quota_patch[mode_key] = QuotaWindow(
                            remaining=new_remaining,
                            total=default.total,
                            window_seconds=default.window_seconds,
                            reset_at=reset_at,
                            synced_at=now,
                            source=QuotaSource.DEFAULT,
                        ).to_dict()
                else:
                    # Console 配额轮换策略：remaining 降到阈值时才启动恢复计时器，
                    # 避免同一批账号被反复选中（评分机制会优先选配额充足的账号）。
                    new_remaining = max(0, existing.remaining - 1)
                    reset_at = existing.reset_at
                    if mode_id == 5:
                        # console 配额：remaining <= 12 时才启动恢复计时器
                        if (
                            reset_at is None
                            and new_remaining <= 12
                            and existing.window_seconds > 0
                        ):
                            reset_at = now + existing.window_seconds * 1000
                    else:
                        # 非 console 模式保持原有逻辑：首次使用即启动计时器
                        if reset_at is None and existing.window_seconds > 0:
                            reset_at = now + existing.window_seconds * 1000
                    quota_patch[mode_key] = QuotaWindow(
                        remaining=new_remaining,
                        total=existing.total,
                        window_seconds=existing.window_seconds,
                        reset_at=reset_at,
                        synced_at=existing.synced_at,
                        source=QuotaSource.ESTIMATED,
                    ).to_dict()
            else:
                logger.debug(
                    "account single-mode quota patch skipped: token={}... pool={} mode_id={} reason=unsupported_mode",
                    record.token[:10],
                    record.pool,
                    mode_id,
                )

        from .commands import AccountPatch

        await self._repo.patch_accounts(
            [
                AccountPatch(
                    token=record.token,
                    last_sync_at=now_ms() if window is not None else None,
                    usage_sync_delta=1 if window is not None else None,
                    usage_use_delta=1 if is_use else None,
                    last_use_at=use_at_ms if is_use else None,
                    **quota_patch,  # type: ignore[arg-type]
                )
            ]
        )

    async def _expire_invalid_credentials(
        self, record: AccountRecord, exc: UpstreamError
    ) -> bool:
        from app.dataplane.reverse.protocol.xai_usage import (
            is_invalid_credentials_error,
        )

        from .invalid_credentials import (
            mark_account_invalid_credentials,
            mark_account_reauth_required,
        )

        # credential_rejected on an SSO-class account is a pre-check reject
        # (e.g. a converted Build credential failed) — the SSO cookie itself
        # may still work on Web/Console, so preserve as REAUTH_REQUIRED.
        # Body-marker-confirmed deaths and Build OAuth 401s stay EXPIRED.
        # A 400 with an invalid-credentials body (not classified as
        # credential_rejected by _classify_upstream_status) must follow the
        # same REAUTH route — otherwise the same marker would EXPIRED on 400
        # but REAUTH on 401/403 (account mis-kill).

        if (
            getattr(exc, "credential_rejected", False)
            or is_invalid_credentials_error(exc)
        ) and record.provider in ("grok_web", "grok_console"):
            from .recovery import bump_reauth_fail_count

            marked = await mark_account_reauth_required(
                self._repo,
                record.token,
                str(exc) or "sso credential rejected",
                source="usage refresh",
            )
            if marked:
                await bump_reauth_fail_count(self._repo, record)
            return marked
        return await mark_account_invalid_credentials(
            self._repo,
            record.token,
            exc,
            source="usage refresh",
        )

    # ------------------------------------------------------------------
    # Console 配额窗口自动重置（后台定时巡检）
    # ------------------------------------------------------------------

    async def reset_expired_console_windows(self) -> int:
        """批量重置过期/卡死的 console 配额（委托给存储后端的 SQL 优化）。

        Returns:
            重置的账号数量。
        """
        count = await self._repo.reset_expired_console_windows()
        if count > 0:
            logger.debug("console quota windows auto-reset: count={}", count)
        return count

    async def recover_console_expired_accounts(self) -> int:
        """自动恢复 console 429 EXPIRED 账号（满足条件）。

        恢复条件（AND）：
        - status = EXPIRED
        - state_reason = console_429_threshold_exceeded
        - usage_use_count > 5（有成功调用历史）
        - expired_at <= now - 1 小时（等待时间够了）

        恢复操作：
        - status: EXPIRED → ACTIVE
        - 清理 ext 中的 expired_at / expired_reason / console_429_count / console_429_last_at

        Returns:
            恢复的账号数量。
        """
        count = await self._repo.recover_console_expired_accounts()
        if count > 0:
            logger.info("console expired accounts auto-recovered: count={}", count)
        return count


__all__ = ["AccountRefreshService", "RefreshResult"]
