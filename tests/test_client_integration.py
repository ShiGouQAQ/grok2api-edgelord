"""客户端集成测试 - 确保CF Clearance不影响现有功能"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.grok.client import GrokClient


@pytest.mark.asyncio
async def test_client_try_with_cf_clearance_disabled():
    """测试CF Clearance禁用时客户端正常工作"""
    with patch('app.services.grok.client.cf_clearance_manager') as mock_manager, \
         patch('app.services.grok.client.token_manager') as mock_token, \
         patch('app.services.grok.client.GrokClient._upload_imgs') as mock_upload, \
         patch('app.services.grok.client.GrokClient._build_payload') as mock_payload, \
         patch('app.services.grok.client.GrokClient._send_request') as mock_send:

        # Mock CF clearance manager (禁用状态)
        mock_manager.ensure_valid_clearance = AsyncMock(return_value=True)

        # Mock token manager
        mock_token.get_token.return_value = "test_token"

        # Mock upload
        mock_upload.return_value = ([], [])

        # Mock payload
        mock_payload.return_value = {"test": "payload"}

        # Mock send request
        mock_send.return_value = {"response": "success"}

        # 执行测试
        result = await GrokClient._try(
            model="grok-3-fast",
            content="test",
            image_urls=[],
            model_name="grok-3-fast",
            model_mode="chat",
            is_video=False,
            stream=False
        )

        # 验证
        assert result == {"response": "success"}
        mock_manager.ensure_valid_clearance.assert_called_once()
        mock_send.assert_called_once()


@pytest.mark.asyncio
async def test_client_try_with_cf_clearance_enabled():
    """测试CF Clearance启用时客户端正常工作"""
    with patch('app.services.grok.client.cf_clearance_manager') as mock_manager, \
         patch('app.services.grok.client.token_manager') as mock_token, \
         patch('app.services.grok.client.GrokClient._upload_imgs') as mock_upload, \
         patch('app.services.grok.client.GrokClient._build_payload') as mock_payload, \
         patch('app.services.grok.client.GrokClient._send_request') as mock_send:

        # Mock CF clearance manager (启用状态)
        mock_manager.ensure_valid_clearance = AsyncMock(return_value=True)

        # Mock token manager
        mock_token.get_token.return_value = "test_token"

        # Mock upload
        mock_upload.return_value = ([], [])

        # Mock payload
        mock_payload.return_value = {"test": "payload"}

        # Mock send request
        mock_send.return_value = {"response": "success"}

        # 执行测试
        result = await GrokClient._try(
            model="grok-3-fast",
            content="test",
            image_urls=[],
            model_name="grok-3-fast",
            model_mode="chat",
            is_video=False,
            stream=False
        )

        # 验证
        assert result == {"response": "success"}
        mock_manager.ensure_valid_clearance.assert_called_once()


@pytest.mark.asyncio
async def test_client_build_headers_with_cf_clearance():
    """测试请求头构建包含cf_clearance"""
    with patch('app.services.grok.client.setting') as mock_setting, \
         patch('app.services.grok.client.get_dynamic_headers') as mock_headers:

        mock_setting.grok_config.get.return_value = "test_clearance_token"
        mock_headers.return_value = {"User-Agent": "test"}

        headers = GrokClient._build_headers("auth_token")

        assert "Cookie" in headers
        assert "auth_token" in headers["Cookie"]
        assert "test_clearance_token" in headers["Cookie"]


@pytest.mark.asyncio
async def test_client_build_headers_without_cf_clearance():
    """测试请求头构建不包含cf_clearance"""
    with patch('app.services.grok.client.setting') as mock_setting, \
         patch('app.services.grok.client.get_dynamic_headers') as mock_headers:

        mock_setting.grok_config.get.return_value = ""
        mock_headers.return_value = {"User-Agent": "test"}

        headers = GrokClient._build_headers("auth_token")

        assert "Cookie" in headers
        assert headers["Cookie"] == "auth_token"
