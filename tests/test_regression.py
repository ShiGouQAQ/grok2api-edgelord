"""回归测试 - 确保新功能不破坏现有功能"""

import pytest
from unittest.mock import patch, AsyncMock


class TestBackwardCompatibility:
    """向后兼容性测试"""

    def test_cf_clearance_manager_import(self):
        """测试CF Clearance管理器可以正常导入"""
        from app.services.grok.cf_clearance import cf_clearance_manager
        assert cf_clearance_manager is not None

    def test_client_import(self):
        """测试客户端可以正常导入"""
        from app.services.grok.client import GrokClient
        assert GrokClient is not None

    def test_config_import(self):
        """测试配置可以正常导入"""
        from app.core.config import setting
        assert setting is not None

    def test_main_app_import(self):
        """测试主应用可以正常导入"""
        from main import app
        assert app is not None


class TestExistingFunctionality:
    """现有功能测试"""

    def test_token_manager_still_works(self):
        """测试token管理器仍然正常工作"""
        from app.services.grok.token import token_manager

        # 验证token管理器存在且有基本方法
        assert hasattr(token_manager, 'get_token')
        assert hasattr(token_manager, 'set_storage')

    def test_config_manager_still_works(self):
        """测试配置管理器仍然正常工作"""
        from app.core.config import setting

        # 验证基本配置功能
        assert hasattr(setting, 'grok_config')
        assert hasattr(setting, 'global_config')

    @pytest.mark.asyncio
    async def test_client_methods_exist(self):
        """测试客户端方法仍然存在"""
        from app.services.grok.client import GrokClient

        # 验证关键方法存在
        assert hasattr(GrokClient, 'openai_to_grok')
        assert hasattr(GrokClient, '_try')
        assert hasattr(GrokClient, '_send_request')
        assert hasattr(GrokClient, '_build_headers')
        assert hasattr(GrokClient, '_build_payload')


class TestNewFeatureIsolation:
    """新功能隔离测试 - 确保新功能不影响现有功能"""

    @pytest.mark.asyncio
    async def test_cf_clearance_disabled_no_impact(self):
        """测试CF Clearance禁用时对现有功能无影响"""
        from app.services.grok.cf_clearance import cf_clearance_manager

        with patch.object(cf_clearance_manager, '_is_enabled', return_value=False):
            # 应该立即返回True，不执行任何操作
            result = await cf_clearance_manager.ensure_valid_clearance()
            assert result is True

    @pytest.mark.asyncio
    async def test_client_works_without_cf_clearance(self):
        """测试客户端在没有CF Clearance的情况下正常工作"""
        from app.services.grok.client import GrokClient

        with patch('app.services.grok.client.cf_clearance_manager') as mock_manager, \
             patch('app.services.grok.client.token_manager') as mock_token, \
             patch('app.services.grok.client.GrokClient._upload_imgs') as mock_upload, \
             patch('app.services.grok.client.GrokClient._build_payload') as mock_payload, \
             patch('app.services.grok.client.GrokClient._send_request') as mock_send:

            mock_manager.ensure_valid_clearance = AsyncMock(return_value=True)
            mock_token.get_token.return_value = "test_token"
            mock_upload.return_value = ([], [])
            mock_payload.return_value = {}
            mock_send.return_value = {"success": True}

            result = await GrokClient._try(
                model="grok-3-fast",
                content="test",
                image_urls=[],
                model_name="grok-3-fast",
                model_mode="chat",
                is_video=False,
                stream=False
            )

            assert result["success"] is True


class TestErrorHandling:
    """错误处理测试"""

    @pytest.mark.asyncio
    async def test_cf_clearance_failure_does_not_crash(self):
        """测试CF Clearance失败不会导致崩溃"""
        from app.services.grok.cf_clearance import cf_clearance_manager

        with patch('app.services.grok.cf_clearance.aiohttp.ClientSession', side_effect=Exception("Test error")):
            # 应该捕获异常并返回False
            result = await cf_clearance_manager._refresh_clearance()
            assert result is False

    @pytest.mark.asyncio
    async def test_client_continues_on_cf_clearance_error(self):
        """测试客户端不受CF Clearance影响（已移除阻塞调用）"""
        from app.services.grok.client import GrokClient

        with patch('app.services.grok.client.token_manager') as mock_token, \
             patch('app.services.grok.client.GrokClient._upload_imgs') as mock_upload, \
             patch('app.services.grok.client.GrokClient._build_payload') as mock_payload, \
             patch('app.services.grok.client.GrokClient._send_request') as mock_send:

            mock_token.get_token.return_value = "test_token"
            mock_upload.return_value = ([], [])
            mock_payload.return_value = {}
            mock_send.return_value = {"success": True}

            # CF Clearance不再在_try中调用，客户端正常工作
            result = await GrokClient._try(
                model="grok-3-fast",
                content="test",
                image_urls=[],
                model_name="grok-3-fast",
                model_mode="chat",
                is_video=False,
                stream=False
            )

            assert result == {"success": True}
