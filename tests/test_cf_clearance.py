"""CF Clearance功能测试"""

import pytest
import time
from unittest.mock import AsyncMock, patch, MagicMock
from app.control.proxy import ProxyDirectory


@pytest.fixture
def directory():
    """创建测试ProxyDirectory实例"""
    import asyncio

    dir = ProxyDirectory.__new__(ProxyDirectory)
    dir._stats = {
        "total_checks": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "solver_success": 0,
        "solver_failures": 0,
        "precheck_skips": 0,
    }
    dir._last_check_time = 0
    dir._check_interval = 3600
    dir._lock = asyncio.Lock()
    dir._clearance_mode = MagicMock()
    dir._clearance_mode.__ne__ = lambda self, other: True
    return dir


@pytest.mark.asyncio
async def test_ensure_valid_clearance_disabled(directory):
    """测试功能禁用时的行为"""
    directory._clearance_mode = MagicMock()
    directory._clearance_mode.__eq__ = lambda self, other: True
    result = await directory.ensure_valid_clearance()
    assert result is True


@pytest.mark.asyncio
async def test_ensure_valid_clearance_cache_hit(directory):
    """测试缓存命中"""
    directory._last_check_time = time.time()

    result = await directory.ensure_valid_clearance()
    assert result is True
    assert directory._stats["cache_hits"] == 1


@pytest.mark.asyncio
async def test_refresh_clearance_failure(directory):
    """测试刷新失败"""
    with patch.object(directory, "_refresh_clearance", return_value=False):
        result = await directory._refresh_clearance()
        assert result is False


def test_get_stats(directory):
    """测试获取统计信息"""
    directory._stats["total_checks"] = 10
    directory._stats["cache_hits"] = 7

    stats = directory.get_stats()
    assert stats["enabled"] is True
    assert stats["hit_rate"] == 0.7
    assert stats["stats"]["total_checks"] == 10


@pytest.mark.asyncio
async def test_refresh_clearance_uses_mihomo_fallback(directory):
    """测试刷新使用 Mihomo 失败切换逻辑"""
    mock_bundle = MagicMock()

    with patch.object(
        directory,
        "_refresh_bundle_with_node_fallback",
        new_callable=AsyncMock,
        return_value=mock_bundle,
    ) as mock_fallback:
        result = await directory._refresh_clearance("https://grok.com")

        assert result is True
        mock_fallback.assert_awaited_once_with(
            affinity_key="cf_clearance",
            proxy_url=directory._get_proxy_url(),
            clearance_origin="https://grok.com",
        )


@pytest.mark.asyncio
async def test_refresh_clearance_returns_false_on_none(directory):
    """测试返回 None 时刷新失败"""
    with patch.object(
        directory,
        "_refresh_bundle_with_node_fallback",
        new_callable=AsyncMock,
        return_value=None,
    ):
        result = await directory._refresh_clearance("https://grok.com")

        assert result is False


@pytest.mark.asyncio
async def test_refresh_clearance_handles_exception(directory):
    """测试刷新异常时返回 False"""
    with patch.object(
        directory,
        "_refresh_bundle_with_node_fallback",
        new_callable=AsyncMock,
        side_effect=Exception("Test error"),
    ):
        result = await directory._refresh_clearance("https://grok.com")

        assert result is False


@pytest.mark.asyncio
async def test_ensure_valid_clearance_force_ignores_cache(directory):
    """测试force=True时忽略缓存"""
    directory._last_check_time = time.time()

    with patch.object(
        directory, "_refresh_clearance", return_value=True
    ) as mock_refresh:
        result = await directory.ensure_valid_clearance(force=True)
        assert result is True
        mock_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_valid_clearance_force_ignores_enabled(directory):
    """测试force=True时忽略启用状态"""
    directory._clearance_mode = MagicMock()
    directory._clearance_mode.__eq__ = lambda self, other: True

    with patch.object(
        directory, "_refresh_clearance", return_value=True
    ) as mock_refresh:
        result = await directory.ensure_valid_clearance(force=True)
        assert result is True
        mock_refresh.assert_called_once()
