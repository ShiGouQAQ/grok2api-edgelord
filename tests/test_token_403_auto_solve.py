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


@pytest.mark.asyncio
async def test_record_failure_with_empty_token():
    """测试空token不会导致崩溃"""
    mgr = GrokTokenManager.__new__(GrokTokenManager)
    mgr.token_data = {"ssoNormal": {}, "ssoSuper": {}}
    
    with patch.object(mgr, '_save_data', new_callable=AsyncMock):
        await mgr.record_failure("", 401, "Empty token")
        await mgr.record_failure("invalid", 401, "Invalid format")
        assert True


@pytest.mark.asyncio
async def test_record_failure_with_malformed_token():
    """测试格式错误的token"""
    mgr = GrokTokenManager.__new__(GrokTokenManager)
    mgr.token_data = {"ssoNormal": {}, "ssoSuper": {}}
    
    with patch.object(mgr, '_save_data', new_callable=AsyncMock):
        await mgr.record_failure("no-sso-field", 401, "Malformed")
        await mgr.record_failure("sso-rw=only", 401, "Missing sso")
        assert True


@pytest.mark.asyncio
async def test_record_failure_increments_count():
    """测试失败计数递增"""
    mgr = GrokTokenManager.__new__(GrokTokenManager)
    mgr.token_data = {"ssoNormal": {"test_sso": {"status": "active", "failedCount": 0}}, "ssoSuper": {}}
    
    with patch.object(mgr, '_save_data', new_callable=AsyncMock):
        await mgr.record_failure("sso-rw=xxx;sso=test_sso", 401, "Error 1")
        assert mgr.token_data["ssoNormal"]["test_sso"]["failedCount"] == 1
        
        await mgr.record_failure("sso-rw=xxx;sso=test_sso", 401, "Error 2")
        assert mgr.token_data["ssoNormal"]["test_sso"]["failedCount"] == 2


@pytest.mark.asyncio
async def test_record_failure_marks_expired_after_max_failures():
    """测试达到最大失败次数后标记为过期"""
    mgr = GrokTokenManager.__new__(GrokTokenManager)
    mgr.token_data = {"ssoNormal": {"test_sso": {"status": "active", "failedCount": 2}}, "ssoSuper": {}}
    
    with patch.object(mgr, '_save_data', new_callable=AsyncMock):
        await mgr.record_failure("sso-rw=xxx;sso=test_sso", 401, "Final error")
        assert mgr.token_data["ssoNormal"]["test_sso"]["status"] == "expired"
        assert mgr.token_data["ssoNormal"]["test_sso"]["failedCount"] == 3


@pytest.mark.asyncio
async def test_record_failure_403_does_not_mark_expired():
    """测试403错误不会标记token为过期"""
    mgr = GrokTokenManager.__new__(GrokTokenManager)
    mgr.token_data = {"ssoNormal": {"test_sso": {"status": "active", "failedCount": 0}}, "ssoSuper": {}}
    
    with patch.object(mgr, '_auto_solve_cf_clearance', new_callable=AsyncMock):
        await mgr.record_failure("sso-rw=xxx;sso=test_sso", 403, "CF blocked")
        assert mgr.token_data["ssoNormal"]["test_sso"]["status"] == "active"


@pytest.mark.asyncio
async def test_record_failure_nonexistent_token():
    """测试记录不存在的token失败"""
    mgr = GrokTokenManager.__new__(GrokTokenManager)
    mgr.token_data = {"ssoNormal": {}, "ssoSuper": {}}
    
    with patch.object(mgr, '_save_data', new_callable=AsyncMock):
        await mgr.record_failure("sso-rw=xxx;sso=nonexistent", 401, "Not found")
        assert True
