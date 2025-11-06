"""Token 403自动求解测试"""

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
    """测试403错误触发自动求解"""
    with patch('app.services.grok.cf_clearance.cf_clearance_manager') as mock_cf_manager:
        mock_cf_manager.ensure_valid_clearance = AsyncMock(return_value=True)

        await token_manager.record_failure("sso-rw=xxx;sso=yyy", 403, "Server blocked")

        # 验证调用了force=True的自动求解
        mock_cf_manager.ensure_valid_clearance.assert_called_once_with(force=True)


@pytest.mark.asyncio
async def test_record_failure_403_auto_solve_success(token_manager):
    """测试403自动求解成功"""
    with patch('app.services.grok.cf_clearance.cf_clearance_manager') as mock_cf_manager:
        with patch('app.services.grok.token.logger') as mock_logger:
            mock_cf_manager.ensure_valid_clearance = AsyncMock(return_value=True)

            await token_manager.record_failure("sso-rw=xxx;sso=yyy", 403, "Server blocked")

            # 验证记录了成功日志
            mock_logger.info.assert_called_once()
            assert "自动求解成功" in str(mock_logger.info.call_args)


@pytest.mark.asyncio
async def test_record_failure_403_auto_solve_failure(token_manager):
    """测试403自动求解失败"""
    with patch('app.services.grok.cf_clearance.cf_clearance_manager') as mock_cf_manager:
        with patch('app.services.grok.token.logger') as mock_logger:
            mock_cf_manager.ensure_valid_clearance = AsyncMock(return_value=False)

            await token_manager.record_failure("sso-rw=xxx;sso=yyy", 403, "Server blocked")

            # 验证记录了失败警告
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            assert any("自动求解失败" in call for call in warning_calls)


@pytest.mark.asyncio
async def test_record_failure_401_no_auto_solve(token_manager):
    """测试401错误不触发自动求解"""
    with patch('app.services.grok.cf_clearance.cf_clearance_manager') as mock_cf_manager:
        mock_cf_manager.ensure_valid_clearance = AsyncMock()

        token_manager.token_data["ssoNormal"]["yyy"] = {"status": "active"}
        await token_manager.record_failure("sso-rw=xxx;sso=yyy", 401, "Token invalid")

        # 验证没有调用自动求解
        mock_cf_manager.ensure_valid_clearance.assert_not_called()
