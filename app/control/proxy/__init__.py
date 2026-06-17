"""ProxyDirectory — control-plane proxy pool coordinator.

Maintains the list of EgressNodes and ClearanceBundles.
Selection delegates to the dataplane ProxyTable; this module owns
configuration loading and clearance refresh lifecycle.
"""

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse

from app.platform.logging.logger import logger
from app.platform.config.snapshot import get_config
from app.platform.runtime.clock import now_ms
from app.platform.runtime.ids import next_hex
from .config import resolve_clearance_config
from .models import (
    EgressMode,
    ClearanceMode,
    EgressNode,
    ClearanceBundle,
    ProxyLease,
    ProxyFeedback,
    ProxyFeedbackKind,
    RequestKind,
    ProxyScope,
)
from .providers.manual import ManualClearanceProvider
from .providers.flaresolverr import FlareSolverrClearanceProvider
from .providers.turnstile import TurnstileClearanceProvider

_DEFAULT_CLEARANCE_ORIGIN = "https://grok.com"
_CONSOLE_CLEARANCE_ORIGIN = "https://console.x.ai"
BundleKey = tuple[str, str]


def _clearance_host(clearance_origin: str | None) -> str:
    host = urlparse(clearance_origin or _DEFAULT_CLEARANCE_ORIGIN).hostname
    return (host or "grok.com").lower()


class ProxyDirectory:
    """Owns egress nodes and clearance bundles.

    Thread-safety: all mutations are protected by ``_lock``.
    """

    def __init__(self) -> None:
        self._nodes: list[EgressNode] = []
        self._resource_nodes: list[EgressNode] = []  # for media downloads
        self._bundles: dict[BundleKey, ClearanceBundle] = {}
        self._lock = asyncio.Lock()
        # Single-flight guard: at most one FlareSolverr call per proxy+host key.
        # Other coroutines wait on the Event until the active refresh completes.
        self._refresh_events: dict[BundleKey, asyncio.Event] = {}
        self._manual = ManualClearanceProvider()
        self._flare = FlareSolverrClearanceProvider()
        self._turnstile = TurnstileClearanceProvider()
        # Lazy import to avoid circular dependency
        from app.dataplane.proxy.mihomo import MihomoClient

        self._mihomo = MihomoClient()
        self._egress_mode: EgressMode = EgressMode.DIRECT
        self._clearance_mode: ClearanceMode = ClearanceMode.NONE
        self._config_sig: tuple | None = None
        # Pool cursor for PROXY_POOL mode: sticky routing with failure-driven rotate.
        # Incremented on node failure; all callers see the same cursor under _lock.
        self._pool_cursor: int = 0
        # Stats and history tracking
        self._stats: dict[str, int] = {
            "total_checks": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "solver_success": 0,
            "solver_failures": 0,
            "precheck_skips": 0,
        }
        self._last_check_time: float = 0
        self._check_interval: int = 3600
        self._init_history_database()

    def _init_history_database(self) -> None:
        """初始化历史记录数据库"""
        db_path = self._get_history_database_path()
        try:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

            conn = sqlite3.connect(db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cf_clearance_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    event_type TEXT NOT NULL,
                    success BOOLEAN NOT NULL,
                    duration REAL,
                    details TEXT,
                    error_message TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_cf_history_timestamp ON cf_clearance_history(timestamp)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_cf_history_event_type ON cf_clearance_history(event_type)"
            )

            conn.commit()
        except Exception as e:
            logger.error(f"[ProxyDirectory] 历史记录数据库初始化失败: {e}")
        finally:
            if "conn" in locals():
                conn.close()

    def _get_history_database_path(self) -> str:
        """获取历史记录数据库路径"""
        data_dir = get_config("storage.data_dir", "data")
        return str(Path(data_dir) / "cf_clearance.db")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def load(self) -> None:
        """Load proxy configuration from the current config snapshot."""
        cfg = get_config()
        egress_mode = EgressMode(cfg.get_str("proxy.egress.mode", "direct"))
        clearance_mode = ClearanceMode.parse(
            cfg.get_str("proxy.clearance.mode", "none")
        )
        base_url = cfg.get_str("proxy.egress.proxy_url", "")
        res_url = cfg.get_str("proxy.egress.resource_proxy_url", "")
        base_pool = tuple(cfg.get_list("proxy.egress.proxy_pool", []))
        res_pool = tuple(cfg.get_list("proxy.egress.resource_proxy_pool", []))
        clearance = resolve_clearance_config(cfg)
        config_sig = (
            egress_mode.value,
            clearance_mode.value,
            base_url,
            res_url,
            base_pool,
            res_pool,
            cfg.get_str("proxy.clearance.flaresolverr_url", ""),
            clearance.cf_cookies,
            clearance.user_agent,
            clearance.cf_clearance,
            clearance.browser,
            cfg.get_int("proxy.clearance.timeout_sec", 60),
        )

        nodes: list[EgressNode] = []
        resource_nodes: list[EgressNode] = []

        if egress_mode in (EgressMode.SINGLE_PROXY, EgressMode.MIHOMO):
            if base_url:
                nodes.append(EgressNode(node_id="single", proxy_url=base_url))
            if res_url:
                resource_nodes.append(
                    EgressNode(node_id="res-single", proxy_url=res_url)
                )

        elif egress_mode == EgressMode.PROXY_POOL:
            for i, url in enumerate(base_pool):
                nodes.append(EgressNode(node_id=f"pool-{i}", proxy_url=url))
            for i, url in enumerate(res_pool):
                resource_nodes.append(
                    EgressNode(node_id=f"res-pool-{i}", proxy_url=url)
                )

        valid_affinities = {n.proxy_url or "direct" for n in [*nodes, *resource_nodes]}
        if not valid_affinities:
            valid_affinities = {"direct"}

        async with self._lock:
            if self._config_sig == config_sig:
                return
            from .models import ClearanceBundleState

            self._egress_mode = egress_mode
            self._clearance_mode = clearance_mode
            self._nodes = nodes
            self._resource_nodes = resource_nodes
            self._pool_cursor = 0
            self._bundles = {
                key: bundle.model_copy(update={"state": ClearanceBundleState.INVALID})
                for key, bundle in self._bundles.items()
                if key[0] in valid_affinities
            }
            self._refresh_events = {
                key: event
                for key, event in self._refresh_events.items()
                if key[0] in valid_affinities
            }
            self._config_sig = config_sig

        logger.info(
            "proxy directory loaded: egress_mode={} clearance_mode={} node_count={} resource_node_count={}",
            egress_mode,
            clearance_mode,
            len(nodes),
            len(resource_nodes),
        )

    # ------------------------------------------------------------------
    # Acquisition
    # ------------------------------------------------------------------

    async def acquire(
        self,
        *,
        scope: ProxyScope = ProxyScope.APP,
        kind: RequestKind = RequestKind.HTTP,
        resource: bool = False,
        clearance_origin: str | None = None,
    ) -> ProxyLease:
        """Return a ProxyLease for the next request.

        For DIRECT mode, returns a lease with no proxy or clearance.
        """
        proxy_url = await self._pick_proxy_url(resource=resource)
        affinity = proxy_url or "direct"
        clearance_host = _clearance_host(clearance_origin)

        bundle = await self._get_or_build_bundle(
            affinity_key=affinity,
            proxy_url=proxy_url or "",
            clearance_origin=clearance_origin or _DEFAULT_CLEARANCE_ORIGIN,
        )

        return ProxyLease(
            lease_id=next_hex(),
            proxy_url=proxy_url,
            cf_cookies=bundle.cf_cookies if bundle else "",
            user_agent=bundle.user_agent if bundle else "",
            clearance_host=clearance_host,
            scope=scope,
            kind=kind,
            acquired_at=now_ms(),
        )

    async def feedback(self, lease: ProxyLease, result: ProxyFeedback) -> None:
        """Apply upstream feedback to the appropriate egress node."""
        if result.kind in (
            ProxyFeedbackKind.CHALLENGE,
            ProxyFeedbackKind.UNAUTHORIZED,
        ):
            # Invalidate associated clearance bundle.
            key = (lease.proxy_url or "direct", lease.clearance_host)
            async with self._lock:
                from .models import ClearanceBundleState

                bundle = self._bundles.get(key)
                if bundle:
                    self._bundles[key] = bundle.model_copy(
                        update={"state": ClearanceBundleState.INVALID}
                    )

        # In PROXY_POOL mode, rotate to the next node on any failure so the
        # next acquire() prefers a different egress rather than hammering the
        # same broken node.
        if (
            self._egress_mode == EgressMode.PROXY_POOL
            and lease.proxy_url
            and result.kind
            in (
                ProxyFeedbackKind.CHALLENGE,
                ProxyFeedbackKind.UNAUTHORIZED,
                ProxyFeedbackKind.FORBIDDEN,
                ProxyFeedbackKind.TRANSPORT_ERROR,
            )
        ):
            async with self._lock:
                self._pool_cursor += 1
                logger.debug(
                    "proxy pool cursor advanced: proxy={} kind={} cursor={}",
                    lease.proxy_url,
                    result.kind,
                    self._pool_cursor,
                )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _pick_proxy_url(self, resource: bool = False) -> str | None:
        if self._egress_mode == EgressMode.DIRECT:
            return None
        async with self._lock:
            # Prefer resource-specific nodes when available; fall back to base nodes.
            nodes = (
                self._resource_nodes
                if resource and self._resource_nodes
                else self._nodes
            )
            if not nodes:
                return None
            if self._egress_mode in (EgressMode.SINGLE_PROXY, EgressMode.MIHOMO):
                return nodes[0].proxy_url
            # PROXY_POOL: sticky routing — use current cursor, rotate on failure.
            idx = self._pool_cursor % len(nodes)
            return nodes[idx].proxy_url

    async def _refresh_bundle_with_node_fallback(
        self,
        *,
        affinity_key: str,
        proxy_url: str,
        clearance_origin: str,
    ) -> ClearanceBundle | None:
        """刷新 clearance bundle，失败时尝试切换代理节点重试。

        流程：
        1. 调用对应 provider（MANUAL/TURNSTILE/FLARESOLVERR）刷新
        2. 如果失败且 Mihomo 启用，切换到最优节点并重试（最多3次）
        3. 如果检测到节点被封禁（NODE_BANNED），直接切换节点并加入黑名单
        """
        start_time = time.time()
        bundle = await self._call_provider(
            affinity_key=affinity_key,
            proxy_url=proxy_url,
            clearance_origin=clearance_origin,
        )
        if bundle:
            duration = time.time() - start_time
            await self.record_event(
                "clearance_refresh",
                success=True,
                duration=duration,
                details={
                    "affinity_key": affinity_key,
                    "clearance_origin": clearance_origin,
                },
            )
            return bundle

        if not self._mihomo._enabled():
            logger.debug("mihomo not enabled, skipping fallback")
            duration = time.time() - start_time
            await self.record_event(
                "clearance_refresh",
                success=False,
                duration=duration,
                details={
                    "affinity_key": affinity_key,
                    "clearance_origin": clearance_origin,
                },
                error_message="provider failed, mihomo not enabled",
            )
            return None

        max_retries = 3
        for attempt in range(max_retries):
            logger.info(
                "clearance refresh failed, attempting mihomo node switch (attempt {}/{})",
                attempt + 1,
                max_retries,
            )
            # Use switch_and_blacklist_current to blacklist the current node
            # This is important because the current node might be banned
            switched = await self._mihomo.switch_and_blacklist_current()
            if not switched:
                logger.warning("mihomo node switch failed")
                duration = time.time() - start_time
                await self.record_event(
                    "clearance_refresh",
                    success=False,
                    duration=duration,
                    details={
                        "affinity_key": affinity_key,
                        "clearance_origin": clearance_origin,
                        "attempt": attempt + 1,
                    },
                    error_message="mihomo node switch failed",
                )
                return None

            logger.info("mihomo node switched, retrying clearance refresh")
            bundle = await self._call_provider(
                affinity_key=affinity_key,
                proxy_url=proxy_url,
                clearance_origin=clearance_origin,
            )
            if bundle:
                duration = time.time() - start_time
                await self.record_event(
                    "clearance_refresh",
                    success=True,
                    duration=duration,
                    details={
                        "affinity_key": affinity_key,
                        "clearance_origin": clearance_origin,
                        "retries": attempt + 1,
                    },
                )
                return bundle

        logger.warning("clearance refresh failed after {} retries", max_retries)
        duration = time.time() - start_time
        await self.record_event(
            "clearance_refresh",
            success=False,
            duration=duration,
            details={
                "affinity_key": affinity_key,
                "clearance_origin": clearance_origin,
                "retries": max_retries,
            },
            error_message=f"failed after {max_retries} retries",
        )
        return None

    async def _call_provider(
        self,
        *,
        affinity_key: str,
        proxy_url: str,
        clearance_origin: str,
    ) -> ClearanceBundle | None:
        """调用对应的 provider 刷新 bundle。"""
        clearance_host = _clearance_host(clearance_origin)

        if self._clearance_mode == ClearanceMode.MANUAL:
            return self._manual.build_bundle(
                affinity_key=affinity_key,
                clearance_host=clearance_host,
            )
        elif self._clearance_mode == ClearanceMode.TURNSTILE:
            return await self._turnstile.refresh_bundle(
                affinity_key=affinity_key,
                proxy_url=proxy_url,
                target_url=clearance_origin,
            )
        else:  # FLARESOLVERR
            return await self._flare.refresh_bundle(
                affinity_key=affinity_key,
                proxy_url=proxy_url,
                target_url=clearance_origin,
            )

    async def _get_or_build_bundle(
        self,
        *,
        affinity_key: str,
        proxy_url: str,
        clearance_origin: str,
    ) -> ClearanceBundle | None:
        if self._clearance_mode == ClearanceMode.NONE:
            return None
        clearance_host = _clearance_host(clearance_origin)
        key: BundleKey = (affinity_key, clearance_host)

        # Single-flight: only one coroutine fetches clearance per proxy+host key.
        # Concurrent callers wait on the Event and retry once it fires.
        while True:
            async with self._lock:
                bundle = self._bundles.get(key)
                if bundle and bundle.state.value == 0:  # VALID
                    return bundle
                event = self._refresh_events.get(key)
                if event is None:
                    # This coroutine wins the right to refresh.
                    event = asyncio.Event()
                    self._refresh_events[key] = event
                    break
            # Another coroutine is already refreshing — wait for it, then retry.
            await event.wait()

        try:
            bundle = await self._refresh_bundle_with_node_fallback(
                affinity_key=affinity_key,
                proxy_url=proxy_url,
                clearance_origin=clearance_origin,
            )
            if bundle:
                async with self._lock:
                    self._bundles[key] = bundle
            return bundle
        finally:
            async with self._lock:
                self._refresh_events.pop(key, None)
            event.set()  # Wake all waiters so they retry with the new bundle.

    # ------------------------------------------------------------------
    # Clearance lifecycle helpers (used by ProxyClearanceScheduler)
    # ------------------------------------------------------------------

    async def invalidate_clearance(self) -> None:
        """Mark all cached clearance bundles as invalid.

        The next ``acquire()`` call for each affinity key will trigger a fresh
        FlareSolverr fetch (serialised by the single-flight guard).
        """
        from .models import ClearanceBundleState

        async with self._lock:
            self._bundles = {
                k: b.model_copy(update={"state": ClearanceBundleState.INVALID})
                for k, b in self._bundles.items()
            }
        logger.debug("clearance bundles invalidated: count={}", len(self._bundles))

    async def warm_up(self) -> None:
        """Pre-fetch clearance bundles for all configured affinity keys.

        Called once at startup so the first real request does not have to wait
        for FlareSolverr.  Does NOT invalidate existing bundles first.
        Pre-warms both grok.com and console.x.ai domains.
        """
        if self._clearance_mode == ClearanceMode.NONE:
            return
        async with self._lock:
            nodes = list(self._nodes)
        affinity_keys = (
            [(n.proxy_url or "direct", n.proxy_url or "") for n in nodes]
            if nodes
            else [("direct", "")]
        )
        # Pre-warm both domains
        origins = [_DEFAULT_CLEARANCE_ORIGIN, _CONSOLE_CLEARANCE_ORIGIN]
        for origin in origins:
            for affinity, proxy_url in affinity_keys:
                await self._get_or_build_bundle(
                    affinity_key=affinity,
                    proxy_url=proxy_url,
                    clearance_origin=origin,
                )

    async def refresh_clearance_safe(self) -> None:
        """Scheduled clearance refresh: build new bundles then swap atomically.

        Unlike ``invalidate_clearance() + warm_up()``, this never discards a
        working bundle before a replacement is ready.  If FlareSolverr is
        temporarily unavailable the old bundle remains valid and continues to
        serve requests.
        """
        if self._clearance_mode == ClearanceMode.NONE:
            return
        async with self._lock:
            nodes = list(self._nodes)
            existing = list(self._bundles.keys())

        refresh_targets: dict[BundleKey, tuple[str, str]] = {}
        default_items = (
            [(n.proxy_url or "direct", n.proxy_url or "") for n in nodes]
            if nodes
            else [("direct", "")]
        )
        for affinity, proxy_url in default_items:
            key: BundleKey = (affinity, _clearance_host(_DEFAULT_CLEARANCE_ORIGIN))
            refresh_targets[key] = (proxy_url, _DEFAULT_CLEARANCE_ORIGIN)
        for key in existing:
            affinity, clearance_host = key
            refresh_targets.setdefault(
                key,
                ("" if affinity == "direct" else affinity, f"https://{clearance_host}"),
            )

        for key, (proxy_url, clearance_origin) in refresh_targets.items():
            affinity, clearance_host = key
            new_bundle = await self._refresh_bundle_with_node_fallback(
                affinity_key=affinity,
                proxy_url=proxy_url,
                clearance_origin=clearance_origin,
            )
            if new_bundle:
                async with self._lock:
                    self._bundles[key] = new_bundle
                logger.debug("clearance bundle refreshed: bundle={}", key)
            else:
                logger.warning(
                    "clearance refresh failed, keeping old bundle: bundle={}",
                    key,
                )

    # ------------------------------------------------------------------
    # Stats and history
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """获取统计信息"""
        total = self._stats["total_checks"]
        return {
            "enabled": self._clearance_mode != ClearanceMode.NONE,
            "cache_valid": self._is_cache_valid(),
            "stats": self._stats.copy(),
            "hit_rate": self._stats["cache_hits"] / max(total, 1),
        }

    def _is_cache_valid(self) -> bool:
        """检查缓存是否有效"""
        if not self._last_check_time:
            return False
        elapsed = time.time() - self._last_check_time
        return elapsed < self._check_interval

    async def _check_cf_challenge(self, test_url: str = "https://grok.com") -> bool:
        """检查是否真的被CF拦截，避免不必要的浏览器启动。

        Returns:
            True: 检测到CF拦截，需要求解
            False: 未检测到CF拦截，无需求解
        """
        import aiohttp

        proxy_url = self._get_proxy_url()

        try:
            async with aiohttp.ClientSession() as session:
                kwargs: dict = {
                    "timeout": aiohttp.ClientTimeout(total=10),
                    "allow_redirects": False,
                }
                if proxy_url:
                    kwargs["proxy"] = proxy_url

                async with session.get(test_url, **kwargs) as resp:
                    if resp.status == 403:
                        logger.debug("[CF Precheck] 检测到403状态码: url={}", test_url)
                        return True

                    if resp.status == 200:
                        text = await resp.text()
                        lower = text.lower()

                        # Check for solvable CF challenge markers
                        if "challenge-platform" in text:
                            logger.debug(
                                "[CF Precheck] 检测到challenge-platform标记: url={}",
                                test_url,
                            )
                            return True

                        # Check for node banned markers - need node switch, not solve
                        _banned_markers = [
                            "attention required!",
                            "ddos protection by cloudflare",
                            "you have been blocked",
                        ]
                        for marker in _banned_markers:
                            if marker in lower:
                                logger.warning(
                                    "[CF Precheck] 检测到节点被封禁标记: marker={} url={}",
                                    marker,
                                    test_url,
                                )
                                # Node is banned, should switch node
                                if self._mihomo._enabled():
                                    await self._mihomo.switch_and_blacklist_current()
                                return True

                    logger.debug(
                        "[CF Precheck] 未检测到CF拦截: url={} status={}",
                        test_url,
                        resp.status,
                    )
                    return False

        except Exception as e:
            logger.debug(
                "[CF Precheck] 检测异常，假设需要求解: url={} error={}", test_url, e
            )
            return True

    def _get_proxy_url(self) -> str:
        """获取当前代理URL"""
        cfg = get_config()
        return str(cfg.get_str("proxy.egress.proxy_url", ""))

    async def ensure_valid_clearance(
        self,
        origin: str = "https://grok.com",
        force: bool = False,
    ) -> bool:
        """确保clearance有效（兼容旧API）

        Args:
            origin: 要检查的域名
            force: 强制刷新
        """
        if not force and self._clearance_mode == ClearanceMode.NONE:
            return True

        self._stats["total_checks"] += 1

        if not force and self._is_cache_valid():
            self._stats["cache_hits"] += 1
            return True

        self._stats["cache_misses"] += 1

        async with self._lock:
            if not force and self._is_cache_valid():
                return True

            success = await self._refresh_clearance(origin)
            if success:
                self._stats["solver_success"] += 1
                self._last_check_time = time.time()
            else:
                self._stats["solver_failures"] += 1

            return success

    async def _refresh_clearance(
        self, clearance_origin: str = "https://grok.com"
    ) -> bool:
        """刷新clearance"""
        try:
            if not await self._check_cf_challenge(clearance_origin):
                logger.info(
                    "[CF Clearance] 预检未发现CF拦截，跳过求解: url={}",
                    clearance_origin,
                )
                self._stats["precheck_skips"] += 1
                self._last_check_time = time.time()
                return True

            bundle = await self._refresh_bundle_with_node_fallback(
                affinity_key="cf_clearance",
                proxy_url=self._get_proxy_url(),
                clearance_origin=clearance_origin,
            )

            if bundle:
                logger.info("[CF Clearance] 刷新成功: url={}", clearance_origin)
                return True

            logger.warning("[CF Clearance] 刷新失败: url={}", clearance_origin)
            return False
        except Exception as e:
            logger.error(f"[CF Clearance] 刷新异常: url={clearance_origin} error={e}")
            return False

    async def record_event(
        self,
        event_type: str,
        success: bool,
        duration: float | None = None,
        details: dict | None = None,
        error_message: str | None = None,
    ) -> None:
        """记录事件到数据库"""
        db_path = self._get_history_database_path()
        conn = sqlite3.connect(db_path, check_same_thread=False)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO cf_clearance_history
                   (timestamp, event_type, success, duration, details, error_message)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    time.time(),
                    event_type,
                    success,
                    duration,
                    json.dumps(details) if details else None,
                    error_message,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    async def get_history(
        self,
        page: int = 1,
        page_size: int = 50,
        event_type: str | None = None,
        start_time: float | None = None,
        end_time: float | None = None,
    ) -> dict:
        """查询历史记录"""
        db_path = self._get_history_database_path()
        conn = sqlite3.connect(db_path, check_same_thread=False)
        try:
            cursor = conn.cursor()

            conditions = []
            params = []

            if event_type:
                conditions.append("event_type = ?")
                params.append(event_type)

            if start_time:
                conditions.append("timestamp >= ?")
                params.append(start_time)

            if end_time:
                conditions.append("timestamp <= ?")
                params.append(end_time)

            where_clause = " AND ".join(conditions) if conditions else "1=1"

            cursor.execute(
                f"SELECT COUNT(*) FROM cf_clearance_history WHERE {where_clause}",
                params,
            )
            total = cursor.fetchone()[0]

            offset = (page - 1) * page_size
            cursor.execute(
                f"""SELECT id, timestamp, event_type, success, duration, details, error_message, created_at
                    FROM cf_clearance_history
                    WHERE {where_clause}
                    ORDER BY timestamp DESC
                    LIMIT ? OFFSET ?""",
                params + [page_size, offset],
            )

            items = []
            for row in cursor.fetchall():
                items.append(
                    {
                        "id": row[0],
                        "timestamp": row[1],
                        "event_type": row[2],
                        "success": bool(row[3]),
                        "duration": row[4],
                        "details": json.loads(row[5]) if row[5] else None,
                        "error_message": row[6],
                        "created_at": row[7],
                    }
                )

            return {
                "total": total,
                "page": page,
                "page_size": page_size,
                "items": items,
            }
        finally:
            conn.close()

    async def clear_history(self) -> None:
        """清空历史记录"""
        db_path = self._get_history_database_path()
        conn = sqlite3.connect(db_path, check_same_thread=False)
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM cf_clearance_history")
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def egress_mode(self) -> EgressMode:
        return self._egress_mode

    @property
    def clearance_mode(self) -> ClearanceMode:
        return self._clearance_mode

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def nodes(self) -> list[EgressNode]:
        """Read-only snapshot of the current egress node list."""
        return list(self._nodes)

    @property
    def bundles(self) -> dict[BundleKey, ClearanceBundle]:
        """Read-only snapshot of the current clearance bundles."""
        return dict(self._bundles)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_directory: ProxyDirectory | None = None


async def get_proxy_directory() -> ProxyDirectory:
    """Return the module-level ProxyDirectory, reloading config if it changed."""
    global _directory
    if _directory is None:
        _directory = ProxyDirectory()
    await _directory.load()
    return _directory


__all__ = [
    "ProxyDirectory",
    "get_proxy_directory",
    "_DEFAULT_CLEARANCE_ORIGIN",
    "_CONSOLE_CLEARANCE_ORIGIN",
]
