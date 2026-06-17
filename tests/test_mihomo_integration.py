"""Mihomo 集成测试：_refresh_bundle_with_node_fallback 降级逻辑。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

# 触发正常导入链，解决循环导入
import app.main  # noqa: F401
from app.control.proxy import ProxyDirectory
from app.control.proxy.models import ClearanceBundle


@pytest.fixture
def directory() -> ProxyDirectory:
    return ProxyDirectory()


@pytest.fixture
def fake_bundle() -> ClearanceBundle:
    return ClearanceBundle(bundle_id="test-bundle", cf_cookies="c=1", user_agent="ua")


@pytest.mark.asyncio
async def test_mihomo_fallback_on_failure(
    directory: ProxyDirectory, fake_bundle: ClearanceBundle
):
    """第一次求解失败、Mihomo 切换成功、第二次求解成功 → 返回 bundle。"""
    call_count = 0

    async def fake_call_provider(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return None
        return fake_bundle

    switch_mock = AsyncMock(return_value=True)

    with (
        patch.object(directory, "_call_provider", side_effect=fake_call_provider),
        patch.object(type(directory._mihomo), "_enabled", return_value=True),
        patch.object(directory._mihomo, "switch_to_optimal", switch_mock),
    ):
        result = await directory._refresh_bundle_with_node_fallback(
            affinity_key="k",
            proxy_url="http://p",
            clearance_origin="https://grok.com",
        )

    assert result is not None
    assert result.bundle_id == "test-bundle"
    assert call_count == 2
    switch_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_mihomo_fallback_when_disabled(directory: ProxyDirectory):
    """Mihomo 未启用 → 返回 None，不调用 switch_to_optimal。"""
    switch_mock = AsyncMock()

    with (
        patch.object(
            directory, "_call_provider", new_callable=AsyncMock, return_value=None
        ),
        patch.object(type(directory._mihomo), "_enabled", return_value=False),
        patch.object(directory._mihomo, "switch_to_optimal", switch_mock),
    ):
        result = await directory._refresh_bundle_with_node_fallback(
            affinity_key="k",
            proxy_url="http://p",
            clearance_origin="https://grok.com",
        )

    assert result is None
    switch_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_mihomo_switch_failure(directory: ProxyDirectory):
    """Mihomo 切换失败 → 返回 None，不重试求解。"""
    call_count = 0

    async def fake_call_provider(**kwargs):
        nonlocal call_count
        call_count += 1
        return None

    with (
        patch.object(directory, "_call_provider", side_effect=fake_call_provider),
        patch.object(type(directory._mihomo), "_enabled", return_value=True),
        patch.object(
            directory._mihomo,
            "switch_to_optimal",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        result = await directory._refresh_bundle_with_node_fallback(
            affinity_key="k",
            proxy_url="http://p",
            clearance_origin="https://grok.com",
        )

    assert result is None
    assert call_count == 1
