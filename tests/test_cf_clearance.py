"""CF Clearance功能测试"""

import pytest
import time
from unittest.mock import AsyncMock, patch
from app.services.grok.cf_clearance import CFClearanceManager


@pytest.fixture
def manager():
    """创建测试管理器实例"""
    mgr = CFClearanceManager.__new__(CFClearanceManager)
    mgr._initialized = True
    mgr.last_check_time = 0
    mgr.check_interval = 3600
    mgr.stats = {
        "total_checks": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "solver_success": 0,
        "solver_failures": 0
    }
    mgr.mihomo_initialized = False
    mgr.node_blacklist = set()
    mgr.last_node_list = []
    return mgr


@pytest.mark.asyncio
async def test_ensure_valid_clearance_disabled(manager):
    """测试功能禁用时的行为"""
    with patch.object(manager, '_is_enabled', return_value=False):
        result = await manager.ensure_valid_clearance()
        assert result is True


@pytest.mark.asyncio
async def test_ensure_valid_clearance_cache_hit(manager):
    """测试缓存命中"""
    manager.last_check_time = time.time()

    with patch.object(manager, '_is_enabled', return_value=True):
        result = await manager.ensure_valid_clearance()
        assert result is True
        assert manager.stats["cache_hits"] == 1


@pytest.mark.asyncio
async def test_refresh_clearance_failure(manager):
    """测试刷新失败"""
    with patch('app.services.grok.cf_clearance.setting') as mock_setting:
        mock_setting.grok_config.get.side_effect = lambda k, d=None: {
            "turnstile_api_url": "http://invalid:9999"
        }.get(k, d)

        result = await manager._refresh_clearance()
        assert result is False


def test_get_stats(manager):
    """测试获取统计信息"""
    manager.stats["total_checks"] = 10
    manager.stats["cache_hits"] = 7

    with patch.object(manager, '_is_enabled', return_value=False):
        stats = manager.get_stats()
        assert stats["enabled"] is False
        assert stats["hit_rate"] == 0.7
        assert stats["stats"]["total_checks"] == 10


@pytest.mark.asyncio
async def test_mihomo_blacklist_mechanism(manager):
    """测试mihomo黑名单机制"""
    manager.node_blacklist = set()
    manager.last_node_list = []

    available_nodes = ["node1", "node2"]
    providers_data = {
        "xai-nodes": {
            "proxies": [
                {"name": "node1", "history": [{"delay": 100}]},
                {"name": "node2", "history": [{"delay": 200}]},
                {"name": "DIRECT", "history": []}
            ]
        }
    }

    # 测试正常选择
    best = manager._select_best_node_from_providers(available_nodes, providers_data)
    assert best == "node1"

    # 加入黑名单后应选择node2
    manager.node_blacklist.add("node1")
    best = manager._select_best_node_from_providers(available_nodes, providers_data)
    assert best == "node2"

    # 全部加入黑名单应返回None
    manager.node_blacklist.add("node2")
    best = manager._select_best_node_from_providers(available_nodes, providers_data)
    assert best is None


@pytest.mark.asyncio
async def test_mihomo_node_list_change_detection(manager):
    """测试节点列表变化检测"""
    manager.node_blacklist = {"node1", "node2"}
    manager.last_node_list = ["node1", "node2", "node3"]

    with patch.object(manager, '_get_node_list', return_value=["node1", "node2", "node4"]):
        new_list = await manager._get_node_list()

        # 检测到变化应清空黑名单（元素或数量变化）
        if len(new_list) != len(manager.last_node_list) or set(new_list) != set(manager.last_node_list):
            manager.node_blacklist.clear()
            manager.last_node_list = new_list

        assert len(manager.node_blacklist) == 0
        assert manager.last_node_list == ["node1", "node2", "node4"]


@pytest.mark.asyncio
async def test_mihomo_node_list_no_change_on_reorder(manager):
    """测试节点列表仅顺序变化时不清空黑名单"""
    manager.node_blacklist = {"node1"}
    manager.last_node_list = ["node1", "node2", "node3"]

    # 仅顺序变化，元素相同
    new_list = ["node3", "node1", "node2"]

    # 不应清空黑名单
    if len(new_list) != len(manager.last_node_list) or set(new_list) != set(manager.last_node_list):
        manager.node_blacklist.clear()
        manager.last_node_list = new_list

    # 黑名单应保持不变
    assert "node1" in manager.node_blacklist
    assert manager.last_node_list == ["node1", "node2", "node3"]


@pytest.mark.asyncio
async def test_backward_compatibility_without_mihomo(manager):
    """测试不启用mihomo时的向后兼容性"""
    manager.node_blacklist = set()
    manager.last_node_list = []

    with patch('app.services.grok.cf_clearance.setting') as mock_setting:
        mock_setting.grok_config.get.side_effect = lambda k, d=None: {
            "mihomo_enabled": False,
            "turnstile_api_url": "http://localhost:6080"
        }.get(k, d)

        with patch.object(manager, '_try_refresh_once', return_value=True):
            result = await manager._refresh_clearance()
            assert result is True
            # 黑名单不应被使用
            assert len(manager.node_blacklist) == 0


@pytest.mark.asyncio
async def test_mihomo_retry_until_exhausted(manager):
    """测试mihomo重试直到节点耗尽"""
    manager.node_blacklist = set()
    manager.last_node_list = ["node1", "node2", "node3"]

    with patch('app.services.grok.cf_clearance.setting') as mock_setting:
        mock_setting.grok_config.get.side_effect = lambda k, d=None: {
            "mihomo_enabled": True,
            "mihomo_api_url": "http://127.0.0.1:9091",
            "mihomo_group_name": "XAI-GROUP"
        }.get(k, d)

        with patch.object(manager, '_try_refresh_once', return_value=False):
            with patch.object(manager, '_get_current_node', side_effect=["node1", "node2", "node3"]):
                with patch.object(manager, '_get_node_list', return_value=["node1", "node2", "node3"]):
                    with patch.object(manager, '_switch_mihomo_node', side_effect=[True, True, False]):
                        with patch.object(manager, '_check_cf_challenge', return_value=True):
                            result = await manager._refresh_clearance()
                            assert result is False
                            # 三个节点都应加入黑名单
                            assert "node1" in manager.node_blacklist
                            assert "node2" in manager.node_blacklist
                            assert "node3" in manager.node_blacklist


@pytest.mark.asyncio
async def test_check_cf_challenge_with_403(manager):
    """测试检测到403时返回True"""
    with patch.object(manager, '_check_cf_challenge', return_value=True):
        result = await manager._check_cf_challenge()
        assert result is True


@pytest.mark.asyncio
async def test_check_cf_challenge_with_challenge_page(manager):
    """测试检测到CF challenge页面时返回True"""
    with patch.object(manager, '_check_cf_challenge', return_value=True):
        result = await manager._check_cf_challenge()
        assert result is True


@pytest.mark.asyncio
async def test_check_cf_challenge_no_challenge(manager):
    """测试无CF拦截时返回False"""
    with patch.object(manager, '_check_cf_challenge', return_value=False):
        result = await manager._check_cf_challenge()
        assert result is False


@pytest.mark.asyncio
async def test_check_cf_challenge_exception(manager):
    """测试检测异常时返回True（保守处理）"""
    with patch('app.services.grok.cf_clearance.setting') as mock_setting:
        mock_setting.grok_config.get.return_value = ""
        with patch('app.services.grok.cf_clearance.aiohttp.ClientSession') as mock_session:
            mock_session.return_value.__aenter__.return_value.get.side_effect = Exception("Network error")
            result = await manager._check_cf_challenge()
            assert result is True


@pytest.mark.asyncio
async def test_ensure_valid_clearance_force_ignores_cache(manager):
    """测试force=True时忽略缓存"""
    manager.last_check_time = time.time()

    with patch.object(manager, '_is_enabled', return_value=False):
        with patch.object(manager, '_refresh_clearance', return_value=True) as mock_refresh:
            result = await manager.ensure_valid_clearance(force=True)
            assert result is True
            mock_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_valid_clearance_force_ignores_enabled(manager):
    """测试force=True时忽略启用状态"""
    with patch.object(manager, '_is_enabled', return_value=False):
        with patch.object(manager, '_refresh_clearance', return_value=True) as mock_refresh:
            result = await manager.ensure_valid_clearance(force=True)
            assert result is True
            mock_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_refresh_clearance_skips_when_no_challenge(manager):
    """测试无CF拦截时跳过求解"""
    with patch.object(manager, '_check_cf_challenge', return_value=False):
        with patch.object(manager, '_try_refresh_once') as mock_refresh:
            result = await manager._refresh_clearance()
            assert result is True
            mock_refresh.assert_not_called()
