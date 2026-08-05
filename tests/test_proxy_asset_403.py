"""ProxyDirectory ASSET-scope 403 handling (Go a05e06a2 port).

A 403 from a public media host (assets.grok.com / imagine-public.x.ai /
imgen.x.ai / vidgen.x.ai …) means the signed object URL expired — it says
nothing about egress node health. ASSET-scope FORBIDDEN feedback must NOT
cool down nodes, advance the pool cursor, or invalidate clearance; all other
kinds keep their current behavior.
"""

import asyncio

import pytest

from app.control.proxy import ProxyDirectory
from app.control.proxy.models import (
    ClearanceBundle,
    ClearanceBundleState,
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

POOL_URLS = ["http://proxy-a", "http://proxy-b"]


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


def _lease(url: str = POOL_URLS[0], scope: ProxyScope = ProxyScope.APP) -> ProxyLease:
    return ProxyLease(lease_id="t", proxy_url=url, scope=scope)


class TestAssetScopeForbidden:
    @pytest.mark.asyncio
    async def test_asset_forbidden_does_not_cool_down_single_node(self):
        directory = _make_directory(EgressMode.SINGLE_PROXY, POOL_URLS[:1])
        node = directory._nodes[0]
        await directory.feedback(
            _lease(scope=ProxyScope.ASSET),
            ProxyFeedback(kind=ProxyFeedbackKind.FORBIDDEN, status_code=403),
        )
        assert node.health == 1.0
        assert node.failure_count == 0
        assert node.state == EgressNodeState.HEALTHY

    @pytest.mark.asyncio
    async def test_asset_forbidden_does_not_advance_pool_cursor(self):
        directory = _make_directory(EgressMode.PROXY_POOL, POOL_URLS)
        await directory.feedback(
            _lease(scope=ProxyScope.ASSET),
            ProxyFeedback(kind=ProxyFeedbackKind.FORBIDDEN, status_code=403),
        )
        assert directory._pool_cursor == 0
        assert all(n.health == 1.0 for n in directory._nodes)

    @pytest.mark.asyncio
    async def test_asset_forbidden_does_not_invalidate_clearance(self):
        directory = _make_directory(EgressMode.SINGLE_PROXY, POOL_URLS[:1])
        key = (POOL_URLS[0], "grok.com")
        directory._bundles = {key: ClearanceBundle(bundle_id="b1")}
        await directory.feedback(
            _lease(scope=ProxyScope.ASSET),
            ProxyFeedback(kind=ProxyFeedbackKind.FORBIDDEN, status_code=403),
        )
        assert directory._bundles[key].state == ClearanceBundleState.VALID

    @pytest.mark.asyncio
    async def test_non_asset_forbidden_still_cools_down(self):
        """Behavior preserved: APP-scope FORBIDDEN keeps cooling the node."""
        directory = _make_directory(EgressMode.SINGLE_PROXY, POOL_URLS[:1])
        node = directory._nodes[0]
        await directory.feedback(
            _lease(scope=ProxyScope.APP),
            ProxyFeedback(kind=ProxyFeedbackKind.FORBIDDEN, status_code=403),
        )
        assert node.health == pytest.approx(0.7)
        assert node.failure_count == 1

    @pytest.mark.asyncio
    async def test_non_asset_forbidden_still_advances_pool_cursor(self):
        """Behavior preserved: APP-scope FORBIDDEN keeps rotating the pool."""
        directory = _make_directory(EgressMode.PROXY_POOL, POOL_URLS)
        await directory.feedback(
            _lease(scope=ProxyScope.APP),
            ProxyFeedback(kind=ProxyFeedbackKind.FORBIDDEN, status_code=403),
        )
        assert directory._pool_cursor == 1

    @pytest.mark.asyncio
    async def test_asset_challenge_keeps_current_behavior(self):
        """Only FORBIDDEN is skipped — a CHALLENGE on asset scope still cools."""
        directory = _make_directory(EgressMode.SINGLE_PROXY, POOL_URLS[:1])
        node = directory._nodes[0]
        await directory.feedback(
            _lease(scope=ProxyScope.ASSET),
            ProxyFeedback(kind=ProxyFeedbackKind.CHALLENGE, status_code=403),
        )
        assert node.health == pytest.approx(0.7)
        assert node.failure_count == 1

    @pytest.mark.asyncio
    async def test_asset_success_keeps_current_behavior(self):
        directory = _make_directory(EgressMode.SINGLE_PROXY, POOL_URLS[:1])
        node = directory._nodes[0]
        await directory.feedback(
            _lease(scope=ProxyScope.ASSET),
            ProxyFeedback(kind=ProxyFeedbackKind.SUCCESS),
        )
        assert node.failure_count == 0
        assert node.health == 1.0  # already maxed; success clamps at 1.0
