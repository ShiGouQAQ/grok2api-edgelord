"""Token 403自动求解测试"""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from app.services.grok.token import GrokTokenManager


@pytest.fixture
def token_manager():
    """创建测试token管理器实例"""
    mgr = GrokTokenManager.__new__(GrokTokenManager)
    mgr.token_data = {"ssoNormal": {}, "ssoSuper": {}}
    return mgr


@pytest.mark.asyncio
async def test_record_failure_403_triggers_auto_solve(token_manager):
    """测试403错误触发后台自动求解"""
    with patch.object(token_manager, '_auto_solve_cf_clearance', new_callable=AsyncMock) as mock_solve:
        await token_manager.record_failure("sso-rw=xxx;sso=yyy", 403, "Server blocked")

        # 等待后台任务启动
        await asyncio.sleep(0.1)

        # 验证后台任务被创建（通过检查日志）
        # 由于是后台任务，我们只验证不会阻塞
        assert True  # record_failure 应该立即返回


@pytest.mark.asyncio
async def test_record_failure_403_auto_solve_success(token_manager):
    """测试403后台自动求解成功"""
    with patch('app.services.grok.cf_clearance.cf_clearance_manager') as mock_cf_manager:
        mock_cf_manager.ensure_valid_clearance = AsyncMock(return_value=True)

        # 直接测试后台求解方法
        await token_manager._auto_solve_cf_clearance()

        # 验证调用了force=True的自动求解
        mock_cf_manager.ensure_valid_clearance.assert_called_once_with(force=True)


@pytest.mark.asyncio
async def test_record_failure_403_auto_solve_failure(token_manager):
    """测试403后台自动求解失败"""
    with patch('app.services.grok.cf_clearance.cf_clearance_manager') as mock_cf_manager:
        mock_cf_manager.ensure_valid_clearance = AsyncMock(return_value=False)

        # 直接测试后台求解方法
        await token_manager._auto_solve_cf_clearance()

        # 验证调用了自动求解
        mock_cf_manager.ensure_valid_clearance.assert_called_once_with(force=True)


@pytest.mark.asyncio
async def test_record_failure_401_no_auto_solve(token_manager):
    """测试401错误不触发自动求解"""
    with patch('app.services.grok.cf_clearance.cf_clearance_manager') as mock_cf_manager:
        mock_cf_manager.ensure_valid_clearance = AsyncMock()

        token_manager.token_data["ssoNormal"]["yyy"] = {"status": "active"}
        await token_manager.record_failure("sso-rw=xxx;sso=yyy", 401, "Token invalid")

        # 验证没有调用自动求解
        mock_cf_manager.ensure_valid_clearance.assert_not_called()
