"""ProxyDirectory egress-node health machine + tunnel rotation tests.

Ports of Go upstream commits:
- 75f4f7a7  rotate Build proxy-pool tunnels per request (fresh_tunnel);
           FeedbackForScope single-0.7 health factor + 401/429/Build/pool skips
- 0893557a  MarkFailureAfterSuccess + node health state machine
- f1867395  cancelled requests must not cool down proxy nodes
- G1-4      3xx classifies as success (Go status >= 200 && status < 400)
"""

import asyncio
from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

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


def _lease(url: str = POOL_URLS[0], scope: ProxyScope = ProxyScope.APP) -> ProxyLease:
    return ProxyLease(lease_id="t", proxy_url=url, scope=scope)


def _make_directory(egress_mode: EgressMode, urls: list[str]) -> ProxyDirectory:
    directory = ProxyDirectory.__new__(ProxyDirectory)
    directory._nodes = [
        EgressNode(node_id=f"n-{i}", proxy_url=url) for i, url in enumerate(urls)
    ]
    directory._resource_nodes = []
    directory._bundles = {}
    directory._refresh_events = {}
    directory._lock = asyncio.Lock()
    directory._egress_mode = egress_mode
    directory._clearance_mode = ClearanceMode.NONE
    directory._pool_cursor = 0
    directory._mihomo = MihomoClient()
    return directory


@pytest.fixture
def pool_directory() -> ProxyDirectory:
    """PROXY_POOL-mode ProxyDirectory with three nodes, no clearance."""
    return _make_directory(EgressMode.PROXY_POOL, POOL_URLS)


@pytest.fixture
def single_directory() -> ProxyDirectory:
    """SINGLE_PROXY-mode ProxyDirectory — one non-pool node."""
    return _make_directory(EgressMode.SINGLE_PROXY, POOL_URLS[:1])


@pytest.fixture
def multi_directory() -> ProxyDirectory:
    """SINGLE_PROXY-mode ProxyDirectory with two non-pool nodes."""
    return _make_directory(EgressMode.SINGLE_PROXY, POOL_URLS[:2])


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
# 0893557a / 75f4f7a7 — EgressNode health state machine (Go FeedbackForScope)
# ---------------------------------------------------------------------------


class TestNodeHealthMachine:
    @pytest.mark.asyncio
    async def test_pool_node_403_skips_health_but_advances_cursor(self, pool_directory):
        """G1-1: Go isProxyPoolNode — a request-level 403 (even NODE_BANNED)
        does not prove a shared pool node unhealthy. Rotation still advances."""
        await pool_directory.feedback(
            _lease(),
            ProxyFeedback(kind=ProxyFeedbackKind.NODE_BANNED, status_code=403),
        )
        node = pool_directory._nodes[0]
        assert node.state == EgressNodeState.HEALTHY
        assert node.health == 1.0
        assert node.failure_count == 0
        assert pool_directory._pool_cursor == 1

    @pytest.mark.asyncio
    async def test_success_repairs_health_and_resets_failure_count(
        self, single_directory
    ):
        await single_directory.feedback(
            _lease(),
            ProxyFeedback(kind=ProxyFeedbackKind.FORBIDDEN, status_code=403),
        )
        node = single_directory._nodes[0]
        assert node.failure_count == 1
        assert node.health == pytest.approx(0.7)
        await single_directory.feedback(
            _lease(), ProxyFeedback(kind=ProxyFeedbackKind.SUCCESS)
        )
        assert node.failure_count == 0
        assert node.health == pytest.approx(0.7 + 0.10)

    @pytest.mark.asyncio
    async def test_success_heals_back_to_healthy(self, single_directory):
        node = single_directory._nodes[0]
        for _ in range(4):
            await single_directory.feedback(
                _lease(), ProxyFeedback(kind=ProxyFeedbackKind.TRANSPORT_ERROR)
            )
        assert node.state == EgressNodeState.UNHEALTHY
        assert node.health == pytest.approx(0.7**4)
        for _ in range(4):
            await single_directory.feedback(
                _lease(), ProxyFeedback(kind=ProxyFeedbackKind.SUCCESS)
            )
        assert node.state == EgressNodeState.HEALTHY
        assert node.health == pytest.approx(0.7**4 + 4 * 0.10)

    @pytest.mark.asyncio
    async def test_mark_failure_after_success_sets_baseline_one(self, single_directory):
        """Stream failure after a successful response header must start a FRESH
        baseline (failure_count=1), not compound prior failures (Go
        MarkFailureAfterSuccess)."""
        node = single_directory._nodes[0]
        for _ in range(2):
            await single_directory.feedback(
                _lease(), ProxyFeedback(kind=ProxyFeedbackKind.TRANSPORT_ERROR)
            )
        assert node.failure_count == 2
        await single_directory.mark_failure_after_success(
            _lease(),
            ProxyFeedback(kind=ProxyFeedbackKind.TRANSPORT_ERROR, status_code=502),
        )
        assert node.failure_count == 1  # baseline, not 3
        assert node.health == pytest.approx(0.7**3)

    @pytest.mark.asyncio
    async def test_healthy_nodes_excludes_degraded(self, multi_directory):
        await multi_directory.feedback(
            _lease(), ProxyFeedback(kind=ProxyFeedbackKind.FORBIDDEN, status_code=403)
        )
        await multi_directory.feedback(
            _lease(), ProxyFeedback(kind=ProxyFeedbackKind.FORBIDDEN, status_code=403)
        )
        table = snapshot_from_directory(multi_directory)
        healthy = table.healthy_nodes()
        assert [n.node_id for n in healthy] == ["n-1"]

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


class TestGoFeedbackForScopeSemantics:
    """G1-1: node health follows Go egress FeedbackForScope @75f4f7a7 —
    single 0.7 factor; 401/429, Build-scope 403/400, and pool/sticky nodes
    never move node health."""

    @pytest.mark.asyncio
    async def test_unauthorized_leaves_health_unchanged(self, single_directory):
        await single_directory.feedback(
            _lease(),
            ProxyFeedback(kind=ProxyFeedbackKind.UNAUTHORIZED, status_code=401),
        )
        node = single_directory._nodes[0]
        assert node.health == 1.0
        assert node.failure_count == 0
        assert node.state == EgressNodeState.HEALTHY

    @pytest.mark.asyncio
    async def test_rate_limited_leaves_health_unchanged(self, single_directory):
        await single_directory.feedback(
            _lease(),
            ProxyFeedback(kind=ProxyFeedbackKind.RATE_LIMITED, status_code=429),
        )
        node = single_directory._nodes[0]
        assert node.health == 1.0
        assert node.failure_count == 0

    @pytest.mark.asyncio
    async def test_build_scope_403_leaves_health_unchanged(self, single_directory):
        await single_directory.feedback(
            _lease(scope=ProxyScope.BUILD),
            ProxyFeedback(kind=ProxyFeedbackKind.FORBIDDEN, status_code=403),
        )
        node = single_directory._nodes[0]
        assert node.health == 1.0
        assert node.failure_count == 0

    @pytest.mark.asyncio
    async def test_build_scope_400_leaves_health_unchanged(self, single_directory):
        """Go: Build 400 is a protocol state (Device OAuth
        authorization_pending polls), not an egress failure."""
        await single_directory.feedback(
            _lease(scope=ProxyScope.BUILD),
            ProxyFeedback(kind=ProxyFeedbackKind.FORBIDDEN, status_code=400),
        )
        node = single_directory._nodes[0]
        assert node.health == 1.0
        assert node.failure_count == 0

    @pytest.mark.asyncio
    async def test_proxy_pool_node_403_skips_health(self, pool_directory):
        await pool_directory.feedback(
            _lease(),
            ProxyFeedback(kind=ProxyFeedbackKind.FORBIDDEN, status_code=403),
        )
        node = pool_directory._nodes[0]
        assert node.health == 1.0
        assert node.failure_count == 0

    @pytest.mark.asyncio
    async def test_proxy_pool_node_transport_error_skips_health(self, pool_directory):
        await pool_directory.feedback(
            _lease(), ProxyFeedback(kind=ProxyFeedbackKind.TRANSPORT_ERROR)
        )
        node = pool_directory._nodes[0]
        assert node.health == 1.0
        assert node.failure_count == 0

    @pytest.mark.asyncio
    async def test_sticky_account_node_skips_health(self):
        """Go isStickyProxyNode: {account}-pinned URLs are exempt too."""
        directory = _make_directory(
            EgressMode.SINGLE_PROXY, ["http://proxy-a/{account}"]
        )
        await directory.feedback(
            _lease(),
            ProxyFeedback(kind=ProxyFeedbackKind.FORBIDDEN, status_code=403),
        )
        node = directory._nodes[0]
        assert node.health == 1.0
        assert node.failure_count == 0

    @pytest.mark.asyncio
    async def test_non_pool_forbidden_applies_0_7(self, single_directory):
        await single_directory.feedback(
            _lease(),
            ProxyFeedback(kind=ProxyFeedbackKind.FORBIDDEN, status_code=403),
        )
        node = single_directory._nodes[0]
        assert node.health == pytest.approx(0.7)
        assert node.failure_count == 1

    @pytest.mark.asyncio
    async def test_transport_error_applies_0_7(self, single_directory):
        await single_directory.feedback(
            _lease(), ProxyFeedback(kind=ProxyFeedbackKind.TRANSPORT_ERROR)
        )
        node = single_directory._nodes[0]
        assert node.health == pytest.approx(0.7)
        assert node.failure_count == 1

    @pytest.mark.asyncio
    async def test_challenge_and_node_banned_follow_403_branch(self, single_directory):
        """Go has no CHALLENGE/NODE_BANNED kinds — both are 403s and hit the
        FORBIDDEN branch (×0.7)."""
        await single_directory.feedback(
            _lease(),
            ProxyFeedback(kind=ProxyFeedbackKind.CHALLENGE, status_code=403),
        )
        await single_directory.feedback(
            _lease(),
            ProxyFeedback(kind=ProxyFeedbackKind.NODE_BANNED, status_code=403),
        )
        node = single_directory._nodes[0]
        assert node.health == pytest.approx(0.7**2)
        assert node.failure_count == 2

    @pytest.mark.asyncio
    async def test_upstream_5xx_leaves_health_unchanged(self, single_directory):
        """Go default branch: HTTP statuses describe the upstream response,
        not proxy endpoint health (account routing handles them)."""
        await single_directory.feedback(
            _lease(),
            ProxyFeedback(kind=ProxyFeedbackKind.UPSTREAM_5XX, status_code=500),
        )
        node = single_directory._nodes[0]
        assert node.health == 1.0
        assert node.failure_count == 0

    @pytest.mark.asyncio
    async def test_499_leaves_health_unchanged(self, single_directory):
        """Go f1867395: clientClosedRequestStatus (499) never cools a node."""
        await single_directory.feedback(
            _lease(),
            ProxyFeedback(kind=ProxyFeedbackKind.TRANSPORT_ERROR, status_code=499),
        )
        node = single_directory._nodes[0]
        assert node.health == 1.0
        assert node.failure_count == 0


class TestClassifyStatusCodeGoSemantics:
    """G1-4: Go `status >= 200 && status < 400` → success, not FORBIDDEN."""

    def test_3xx_classifies_as_success(self):
        from app.control.proxy.feedback import classify_status_code

        for code in (200, 204, 301, 302, 304, 399):
            assert classify_status_code(code) == ProxyFeedbackKind.SUCCESS
        assert classify_status_code(400) == ProxyFeedbackKind.FORBIDDEN
        assert classify_status_code(404) == ProxyFeedbackKind.FORBIDDEN


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


# ---------------------------------------------------------------------------
# 0893557a — mark_failure_after_success wired into the stream seam
# ---------------------------------------------------------------------------


class TestStreamFailureAfterSuccessWiring:
    """G1-2(b): a body stream that fails AFTER a successful response header
    must reach mark_failure_after_success (Go 0893557a isUpstreamStreamFailure
    → MarkFailureAfterSuccess(502)); a client cancel must not."""

    @pytest.fixture
    def _bypass_dpop_exchange(self, monkeypatch):
        """Serve a ready DPoP session instead of running the token exchange."""
        import time

        from cryptography.hazmat.primitives.asymmetric import ec

        from app.dataplane.reverse.protocol.dpop import (
            DPoPSession,
            public_dpop_jwk,
        )

        key = ec.generate_private_key(ec.SECP256R1())
        session = DPoPSession(
            access_token="fake-at",
            private_key=key,
            public_jwk=public_dpop_jwk(key),
            expires_at=int(time.time() * 1000) + 3_600_000,
        )
        fake = MagicMock()
        fake.get_or_fetch = AsyncMock(return_value=session)
        monkeypatch.setattr(
            "app.dataplane.reverse.protocol.xai_console_chat._get_dpop_manager",
            lambda token, lease: fake,
        )

    @contextmanager
    def _patched_stream(
        self, aiter_lines
    ) -> Iterator[tuple[AsyncMock, AsyncGenerator[tuple[str, str], None]]]:
        from app.dataplane.reverse.protocol.xai_console_chat import (
            stream_console_chat,
        )

        mock_proxy = AsyncMock()
        mock_lease = MagicMock()
        mock_lease.clearance_host = "console.x.ai"
        mock_proxy.acquire.return_value = mock_lease

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aiter_lines = aiter_lines

        mock_session = AsyncMock()
        mock_session.post.return_value = mock_response
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.dataplane.proxy.get_proxy_runtime", return_value=mock_proxy),
            patch(
                "app.dataplane.proxy.adapters.session.ResettableSession",
                return_value=mock_session,
            ),
            patch(
                "app.dataplane.proxy.adapters.headers.build_console_headers",
                return_value={},
            ),
            patch(
                "app.dataplane.proxy.adapters.session.build_session_kwargs",
                return_value={},
            ),
        ):
            payload = {"model": "grok-4.3", "input": []}
            yield mock_proxy, stream_console_chat("test-token", payload)

    @pytest.mark.asyncio
    async def test_stream_read_failure_after_200_marks_failure(
        self, _bypass_dpop_exchange
    ):
        from app.platform.errors import UpstreamError

        async def broken_lines():
            yield b"data: {}"
            raise OSError("connection reset")

        with self._patched_stream(broken_lines) as (mock_proxy, gen):
            with pytest.raises(UpstreamError, match="Console stream read failed"):
                async for _ in gen:
                    pass

            # 200 header → success feedback first, then mark_failure_after_success.
            assert mock_proxy.feedback.call_count == 1
            assert mock_proxy.feedback.call_args[0][1].kind == ProxyFeedbackKind.SUCCESS
            assert mock_proxy.mark_failure_after_success.call_count == 1
            fb = mock_proxy.mark_failure_after_success.call_args[0][1]
            assert fb.kind == ProxyFeedbackKind.TRANSPORT_ERROR
            assert fb.status_code == 502

    @pytest.mark.asyncio
    async def test_stream_cancel_after_200_does_not_mark_failure(
        self, _bypass_dpop_exchange
    ):
        """G1-2(a): asyncio.CancelledError is a BaseException — the seam's
        `except Exception` must not catch it, so a cancelled stream sends no
        failure feedback (Go f1867395 499/cancel skip)."""

        async def cancelled_lines():
            yield b"data: {}"
            raise asyncio.CancelledError

        with self._patched_stream(cancelled_lines) as (mock_proxy, gen):
            with pytest.raises(asyncio.CancelledError):
                async for _ in gen:
                    pass

            assert mock_proxy.mark_failure_after_success.call_count == 0
            assert mock_proxy.feedback.call_count == 1  # only the 200 header
