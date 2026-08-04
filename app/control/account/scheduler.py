"""Background scheduler for periodic account quota refresh.

Runs one independent loop per pool type (basic / super / heavy / build),
each with its own configurable interval read from:

    account.refresh.basic_interval_sec  (default 86400 — 24 h)
    account.refresh.super_interval_sec  (default  7200 —  2 h)
    account.refresh.heavy_interval_sec  (default  7200 —  2 h)
    account.refresh.build_interval_sec  (default 21600 —  6 h)
"""

import asyncio
import time

from app.platform.config.snapshot import get_config
from app.platform.logging.logger import logger
from .enums import AccountStatus
from .refresh import AccountRefreshService

# Pool → (config key, built-in default seconds)
_POOL_CONFIG: dict[str, tuple[str, int]] = {
    "basic": ("account.refresh.basic_interval_sec", 86_400),
    "super": ("account.refresh.super_interval_sec", 7_200),
    "heavy": ("account.refresh.heavy_interval_sec", 7_200),
    "build": ("account.refresh.build_interval_sec", 21_600),
}


def _interval(pool: str) -> int:
    key, default = _POOL_CONFIG[pool]
    v = get_config(key, None)
    return int(v) if v is not None else default


class AccountRefreshScheduler:
    """Runs one refresh loop per pool type at pool-specific intervals.

    Lifecycle:  ``start()`` → loops run in background → ``stop()`` to cancel.
    """

    def __init__(self, refresh_service: AccountRefreshService) -> None:
        self._service = refresh_service
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()

    def bind_service(self, refresh_service: AccountRefreshService) -> None:
        """Update the refresh service used by the singleton scheduler."""
        self._service = refresh_service

    def is_running(self) -> bool:
        """Return True while any pool refresh loop is still active."""
        return any(not task.done() for task in self._tasks)

    def start(self) -> None:
        if self.is_running():
            return
        self._stop.clear()
        self._tasks = [
            asyncio.create_task(self._loop(pool), name=f"account-refresh-{pool}")
            for pool in _POOL_CONFIG
        ]
        self._tasks.append(
            asyncio.create_task(
                self._build_credential_loop(),
                name="build-credential-refresh",
            )
        )
        self._tasks.append(
            asyncio.create_task(
                self._run_startup_recovery(),
                name="build-startup-recovery",
            )
        )
        intervals = {p: _interval(p) for p in _POOL_CONFIG}
        logger.info(
            "account refresh scheduler started: basic_interval_s={} super_interval_s={} heavy_interval_s={} build_interval_s={}",
            intervals["basic"],
            intervals["super"],
            intervals["heavy"],
            intervals["build"],
        )

    def stop(self) -> None:
        was_running = self.is_running()
        self._stop.set()
        for t in self._tasks:
            if not t.done():
                t.cancel()
        self._tasks = []
        if was_running:
            logger.info("account refresh scheduler stopped")

    async def _loop(self, pool: str) -> None:
        while not self._stop.is_set():
            interval = _interval(pool)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=float(interval))
                break  # stop event fired
            except asyncio.TimeoutError:
                pass

            if self._stop.is_set():
                break

            try:
                result = await self._service.refresh_scheduled(pool=pool)
                logger.info(
                    "account refresh cycle completed: pool={} checked={} refreshed={} recovered={} failed={}",
                    pool,
                    result.checked,
                    result.refreshed,
                    result.recovered,
                    result.failed,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "account refresh cycle failed: pool={} error_type={} error={}",
                    pool,
                    type(exc).__name__,
                    exc,
                )

    async def _build_credential_loop(self) -> None:
        from .build_refresh import compute_refresh_due_at, refresh_build_token
        from .commands import AccountPatch

        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=60.0)
                break
            except asyncio.TimeoutError:
                pass

            if self._stop.is_set():
                break

            try:
                snapshot = await self._service._repo.runtime_snapshot()
                now = time.time() * 1000
                refresh_needed = []

                for record in snapshot.items:
                    if record.is_deleted() or record.status != "active":
                        continue
                    if record.provider != "grok_build":
                        continue
                    ext = record.ext or {}
                    refresh_token_val = ext.get("build_refresh_token", "")
                    refresh_due_at = ext.get("build_refresh_due_at", 0)
                    if not refresh_token_val or not refresh_due_at:
                        continue
                    if now >= refresh_due_at:
                        refresh_needed.append(record)

                if not refresh_needed:
                    continue

                logger.info(
                    "build credential refresh cycle: accounts_due={}",
                    len(refresh_needed),
                )

                for record in refresh_needed:
                    try:
                        ext = record.ext or {}
                        new_tokens = await refresh_build_token(
                            ext.get("build_refresh_token", "")
                        )
                        if new_tokens is None:
                            build_expires_at = ext.get("build_expires_at", 0)
                            now_ms = int(time.time() * 1000)
                            if build_expires_at > now_ms:
                                # Access token still valid — defer re-auth to expiry.
                                # Port of Go's resolvePermanentRefreshFailure().
                                logger.info(
                                    "build refresh token permanently invalid but access token still valid"
                                    " (expires_at={} > now={}), deferring re-auth until expiry",
                                    build_expires_at,
                                    now_ms,
                                )
                                new_ext = {
                                    **ext,
                                    "build_refresh_permanent": True,
                                    "build_refresh_error": "refresh_token_invalid",
                                    "build_refresh_due_at": build_expires_at,
                                }
                                await self._service._repo.patch_accounts(
                                    [
                                        AccountPatch(
                                            token=record.token, ext_merge=new_ext
                                        )
                                    ]
                                )
                            else:
                                # Access token also expired — disable the account
                                logger.warning(
                                    "build token refresh permanently failed and access token expired:"
                                    " token={}...",
                                    record.token[:10],
                                )
                                await self._service._repo.patch_accounts(
                                    [
                                        AccountPatch(
                                            token=record.token,
                                            status=AccountStatus.DISABLED,
                                            state_reason="build_refresh_permanent_failure",
                                        )
                                    ]
                                )
                            continue

                        new_expires_at = now + new_tokens.expires_in * 1000
                        new_due_at = int(
                            compute_refresh_due_at(new_expires_at / 1000, record.token)
                            * 1000
                        )

                        new_ext = {
                            **ext,
                            "build_access_token": new_tokens.access_token,
                            "build_refresh_token": new_tokens.refresh_token
                            or ext.get("build_refresh_token", ""),
                            "build_id_token": new_tokens.id_token
                            or ext.get("build_id_token", ""),
                            "build_expires_at": int(new_expires_at),
                            "build_refresh_due_at": new_due_at,
                        }

                        await self._service._repo.patch_accounts(
                            [AccountPatch(token=record.token, ext_merge=new_ext)]
                        )

                        logger.info(
                            "build token refreshed: token={}...",
                            record.token[:10],
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning(
                            "build token refresh failed: token={}... error={}",
                            record.token[:10],
                            exc,
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "build credential refresh cycle failed: error_type={} error={}",
                    type(exc).__name__,
                    exc,
                )

    async def recover_build_tokens(self) -> int:
        from .build_refresh import compute_refresh_due_at, refresh_build_token
        from .commands import AccountPatch

        snapshot = await self._service._repo.runtime_snapshot()
        now = time.time() * 1000
        recovery_threshold = now + 10 * 60 * 1000
        refreshed = 0

        for record in snapshot.items:
            if record.is_deleted() or record.status != "active":
                continue
            if record.provider != "grok_build":
                continue
            ext = record.ext or {}
            expires_at = ext.get("build_expires_at", 0)
            if expires_at <= 0 or expires_at > recovery_threshold:
                continue

            refresh_token_val = ext.get("build_refresh_token", "")
            if not refresh_token_val:
                continue

            try:
                new_tokens = await refresh_build_token(refresh_token_val)
                if new_tokens is None:
                    build_expires_at = ext.get("build_expires_at", 0)
                    if build_expires_at > now:
                        # Access token still valid — defer re-auth to expiry
                        new_ext = {
                            **ext,
                            "build_refresh_permanent": True,
                            "build_refresh_error": "refresh_token_invalid",
                            "build_refresh_due_at": build_expires_at,
                        }
                        await self._service._repo.patch_accounts(
                            [AccountPatch(token=record.token, ext_merge=new_ext)]
                        )
                    else:
                        # Access token expired too — disable the account
                        await self._service._repo.patch_accounts(
                            [
                                AccountPatch(
                                    token=record.token,
                                    status=AccountStatus.DISABLED,
                                    state_reason="build_refresh_permanent_failure",
                                )
                            ]
                        )
                    continue

                new_expires_at = now + new_tokens.expires_in * 1000
                new_due_at = int(
                    compute_refresh_due_at(new_expires_at / 1000, record.token) * 1000
                )

                new_ext = {
                    **ext,
                    "build_access_token": new_tokens.access_token,
                    "build_refresh_token": new_tokens.refresh_token
                    or refresh_token_val,
                    "build_id_token": new_tokens.id_token
                    or ext.get("build_id_token", ""),
                    "build_expires_at": int(new_expires_at),
                    "build_refresh_due_at": new_due_at,
                }

                await self._service._repo.patch_accounts(
                    [AccountPatch(token=record.token, ext_merge=new_ext)]
                )

                refreshed += 1
                logger.info(
                    "build token startup recovery: token={}...",
                    record.token[:10],
                )
            except Exception as exc:
                logger.warning(
                    "build token startup recovery failed: token={}... error={}",
                    record.token[:10],
                    exc,
                )

        if refreshed:
            logger.info(
                "build token startup recovery completed: refreshed={}",
                refreshed,
            )
        return refreshed

    async def _run_startup_recovery(self) -> None:
        try:
            count = await self.recover_build_tokens()
            if count:
                logger.info("build startup recovery completed: refreshed={}", count)
        except Exception as exc:
            logger.warning("build startup recovery failed: error={}", exc)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_scheduler: AccountRefreshScheduler | None = None


def get_account_refresh_scheduler(
    refresh_service: AccountRefreshService,
) -> AccountRefreshScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AccountRefreshScheduler(refresh_service)
    else:
        _scheduler.bind_service(refresh_service)
    return _scheduler


__all__ = ["AccountRefreshScheduler", "get_account_refresh_scheduler"]
