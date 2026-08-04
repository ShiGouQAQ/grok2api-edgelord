"""ProxyDirectory egress-node health machine + tunnel rotation tests.

Ports of Go upstream commits:
- 75f4f7a7  rotate Build proxy-pool tunnels per request (fresh_tunnel)
- 0893557a  MarkFailureAfterSuccess + node health state machine
- f1867395  cancelled requests must not cool down proxy nodes
"""

import asyncio
from typing import AsyncGenerator

import pytest
from curl_cffi.const import CurlOpt

from app.control.proxy import ProxyDirectory
from app.control.proxy.models import (
    ClearanceMode,
    EgressMode,
    EgressNode,
    EgressNodeState,
    ProxyFeedback,
    ProxyFeedbackKind,
    ProxyLease,
    ProxyScope,
)
from app.dataplane.proxy.mihomo import MihomoClient
from app.dataplane.proxy.table import snapshot_from_directory

POOL_URLS = ["http://proxy-a", "http://proxy-b", "http://proxy-c"]


def _lease(url: str = POOL_URLS[0]) -> ProxyLease:
    return ProxyLease(lease_id="t", proxy_url=url)


@pytest.fixture
def pool_directory() -> ProxyDirectory:
    """PROXY_POOL-mode ProxyDirectory with three nodes, no clearance."""
    directory = ProxyDirectory.__new__(ProxyDirectory)
    directory._nodes = [
        EgressNode(node_id=f"pool-{i}", proxy_url=url)
        for i, url in enumerate(POOL_URLS)
    ]
    directory._resource_nodes = []
    directory._bundles = {}
    directory._refresh_events = {}
    directory._lock = asyncio.Lock()
    directory._egress_mode = EgressMode.PROXY_POOL
    directory._clearance_mode = ClearanceMode.NONE
    directory._pool_cursor = 0
    directory._mihomo = MihomoClient()
    return directory


# ---------------------------------------------------------------------------
# 75f4f7a7 — Build proxy-pool tunnel rotation
# ---------------------------------------------------------------------------


class TestBuildTunnelRotation:
    @pytest.mark.asyncio
    async def test_build_pool_acquire_rotates_url_per_request(self, pool_directory):
        leases = [
            await pool_directory.acquire(scope=ProxyScope.BUILD) for _ in range(3)
        ]
        assert [l.proxy_url for l in leases] == POOL_URLS
        assert all(l.fresh_tunnel for l in leases)

    @pytest.mark.asyncio
    async def test_app_scope_acquire_is_sticky_no_rotation(self, pool_directory):
        first = await pool_directory.acquire()
        second = await pool_directory.acquire()
        assert first.proxy_url == second.proxy_url == POOL_URLS[0]
        assert first.fresh_tunnel is False
        assert second.fresh_tunnel is False
        assert pool_directory._pool_cursor == 0

    @pytest.mark.asyncio
    async def test_sticky_account_placeholder_keeps_pinning(self, pool_directory):
        """Proxy URLs carrying the account placeholder must never rotate
        (Go: sticky := strings.Contains(proxyURL, "{account}"))."""
        pool_directory._nodes = [
            EgressNode(node_id="pool-0", proxy_url="http://proxy-a/{account}")
        ]
        first = await pool_directory.acquire(scope=ProxyScope.BUILD)
        second = await pool_directory.acquire(scope=ProxyScope.BUILD)
        assert first.proxy_url == second.proxy_url == "http://proxy-a/{account}"
        assert first.fresh_tunnel is False
        assert pool_directory._pool_cursor == 0

    @pytest.mark.asyncio
    async def test_failure_feedback_still_rotates_in_pool_mode(self, pool_directory):
        """Failure-driven cursor advance must keep working for pool mode."""
        await pool_directory.feedback(
            _lease(), ProxyFeedback(kind=ProxyFeedbackKind.NODE_BANNED)
        )
        assert pool_directory._pool_cursor == 1

    @pytest.mark.asyncio
    async def test_fresh_tunnel_lease_forces_new_connection(self):
        """fresh_tunnel must be honoured at the transport consumption point:
        curl_cffi gets FRESH_CONNECT + FORBID_REUSE (Go request.Close=true)."""
        from app.dataplane.proxy.adapters.session import build_session_kwargs

        lease = ProxyLease(lease_id="t", proxy_url=POOL_URLS[0], fresh_tunnel=True)
        kwargs = build_session_kwargs(lease=lease)
        opts = kwargs["curl_options"]
        assert opts[CurlOpt.FRESH_CONNECT] == 1
        assert opts[CurlOpt.FORBID_REUSE] == 1

    @pytest.mark.asyncio
    async def test_non_fresh_lease_keeps_connection_reuse(self):
        from app.dataplane.proxy.adapters.session import build_session_kwargs

        lease = ProxyLease(lease_id="t", proxy_url=POOL_URLS[0])
        kwargs = build_session_kwargs(lease=lease)
        assert kwargs.get("curl_options", {}).get(CurlOpt.FRESH_CONNECT) != 1


# ---------------------------------------------------------------------------
# 0893557a — EgressNode health state machine
# ---------------------------------------------------------------------------


class TestNodeHealthMachine:
    @pytest.mark.asyncio
    async def test_node_banned_degrades_node_and_advances_cursor(self, pool_directory):
        await pool_directory.feedback(
            _lease(), ProxyFeedback(kind=ProxyFeedbackKind.NODE_BANNED)
        )
        node = pool_directory._nodes[0]
        assert node.state == EgressNodeState.DEGRADED
        assert node.health == pytest.approx(0.3)
        assert node.failure_count == 1
        assert pool_directory._pool_cursor == 1

    @pytest.mark.asyncio
    async def test_success_repairs_health_and_resets_failure_count(
        self, pool_directory
    ):
        await pool_directory.feedback(
            _lease(), ProxyFeedback(kind=ProxyFeedbackKind.NODE_BANNED)
        )
        await pool_directory.feedback(
            _lease(), ProxyFeedback(kind=ProxyFeedbackKind.SUCCESS)
        )
        node = pool_directory._nodes[0]
        assert node.failure_count == 0
        assert node.health == pytest.approx(0.3 + 0.12)
        assert node.state == EgressNodeState.DEGRADED

    @pytest.mark.asyncio
    async def test_success_heals_back_to_healthy(self, pool_directory):
        node = pool_directory._nodes[0]
        for _ in range(3):
            await pool_directory.feedback(
                _lease(), ProxyFeedback(kind=ProxyFeedbackKind.TRANSPORT_ERROR)
            )
        assert node.state == EgressNodeState.UNHEALTHY
        assert node.health == pytest.approx(0.125)
        for _ in range(6):
            await pool_directory.feedback(
                _lease(), ProxyFeedback(kind=ProxyFeedbackKind.SUCCESS)
            )
        assert node.state == EgressNodeState.HEALTHY
        assert node.health > 0.125

    @pytest.mark.asyncio
    async def test_mark_failure_after_success_sets_baseline_one(self, pool_directory):
        """Stream failure after a successful response header must start a FRESH
        baseline (failure_count=1), not compound prior failures (Go
        MarkFailureAfterSuccess)."""
        node = pool_directory._nodes[0]
        for _ in range(2):
            await pool_directory.feedback(
                _lease(), ProxyFeedback(kind=ProxyFeedbackKind.TRANSPORT_ERROR)
            )
        assert node.failure_count == 2
        await pool_directory.mark_failure_after_success(
            _lease(), ProxyFeedback(kind=ProxyFeedbackKind.TRANSPORT_ERROR)
        )
        assert node.failure_count == 1  # baseline, not 3
        assert node.health < 1.0

    @pytest.mark.asyncio
    async def test_healthy_nodes_excludes_degraded(self, pool_directory):
        await pool_directory.feedback(
            _lease(), ProxyFeedback(kind=ProxyFeedbackKind.NODE_BANNED)
        )
        table = snapshot_from_directory(pool_directory)
        healthy = table.healthy_nodes()
        assert [n.node_id for n in healthy] == ["pool-1", "pool-2"]

    @pytest.mark.asyncio
    async def test_feedback_without_node_logs_instead_of_silent_drop(
        self, pool_directory, monkeypatch
    ):
        """Mark-failure writes must not silently drop (Go logs
        stream_failure_health_write_failed)."""
        import app.control.proxy as proxy_mod

        warnings = []
        monkeypatch.setattr(
            proxy_mod.logger, "warning", lambda *a, **k: warnings.append(a)
        )
        await pool_directory.feedback(
            _lease(url="http://unknown"),
            ProxyFeedback(kind=ProxyFeedbackKind.NODE_BANNED),
        )
        assert any("proxy node failure write failed" in str(w) for w in warnings)
        assert pool_directory._pool_cursor == 1  # rotation still applies


# ---------------------------------------------------------------------------
# f1867395 — cancelled requests must not cool down proxy nodes
# ---------------------------------------------------------------------------


class TestCancelNoCooldown:
    @pytest.mark.asyncio
    async def test_cancelled_request_propagates_without_feedback(
        self, pool_directory, monkeypatch
    ):
        """Drive the real transport wrapper (post_stream): its `except
        Exception` must NOT catch CancelledError (a BaseException), so the
        cancellation propagates and proxy feedback is never delivered."""
        import app.dataplane.reverse.transport.http as http_mod

        class FakeSession:
            def __init__(self, **kwargs):
                pass

            async def post(self, *args, **kwargs):
                raise asyncio.CancelledError

            async def close(self):
                pass

        monkeypatch.setattr(http_mod, "ResettableSession", lambda **k: FakeSession())
        task = asyncio.create_task(
            http_mod.post_stream("https://grok.com/x", "tok", b"{}")
        )
        with pytest.raises(asyncio.CancelledError):
            await task

        # No cooldown: node untouched, cursor untouched, no blacklist entry.
        node = pool_directory._nodes[0]
        assert node.state == EgressNodeState.HEALTHY
        assert node.health == 1.0
        assert node.failure_count == 0
        assert pool_directory._pool_cursor == 0
        assert pool_directory._mihomo._blacklist == set()
