"""测试403错误类型检测（Token失效 vs CF盾）"""
import pytest
from unittest.mock import Mock, AsyncMock, patch
from app.services.grok.client import GrokClient


@pytest.mark.asyncio
async def test_403_token_invalid_json_response():
    """测试403 JSON响应识别为Token失效"""
    mock_response = Mock()
    mock_response.status_code = 403
    mock_response.text = '{"error":{"code":7,"message":"User is blocked: Bot/distillation [WKE=unauthorized:blocked-user]"}}'
    mock_response.json.return_value = {"error": {"code": 7, "message": "User is blocked: Bot/distillation [WKE=unauthorized:blocked-user]"}}

    with patch('app.services.grok.client.token_manager') as mock_tm:
        mock_tm.record_failure = AsyncMock()

        with pytest.raises(Exception) as exc_info:
            await GrokClient._handle_error(mock_response, "sso-rw=test;sso=test")

        # 验证记录为401（Token失效）
        mock_tm.record_failure.assert_called_once()
        call_args = mock_tm.record_failure.call_args[0]
        assert call_args[1] == 401  # status_code
        assert "Token blocked/invalid" in call_args[2]  # error_message


@pytest.mark.asyncio
async def test_403_cf_challenge_html_response():
    """测试403 HTML响应识别为CF盾"""
    mock_response = Mock()
    mock_response.status_code = 403
    mock_response.text = '<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>challenge</body></html>'
    mock_response.json.side_effect = ValueError("Not JSON")

    with patch('app.services.grok.client.token_manager') as mock_tm:
        mock_tm.record_failure = AsyncMock()

        with pytest.raises(Exception) as exc_info:
            await GrokClient._handle_error(mock_response, "sso-rw=test;sso=test")

        # 验证记录为403（CF盾）
        mock_tm.record_failure.assert_called_once()
        call_args = mock_tm.record_failure.call_args[0]
        assert call_args[1] == 403  # status_code
        assert "服务器IP被Block" in call_args[2]  # error_message


@pytest.mark.asyncio
async def test_403_token_expired_keyword():
    """测试包含expired关键词的403识别为Token失效"""
    mock_response = Mock()
    mock_response.status_code = 403
    mock_response.text = '{"error":"Token expired"}'
    mock_response.json.return_value = {"error": "Token expired"}

    with patch('app.services.grok.client.token_manager') as mock_tm:
        mock_tm.record_failure = AsyncMock()

        with pytest.raises(Exception):
            await GrokClient._handle_error(mock_response, "sso-rw=test;sso=test")

        call_args = mock_tm.record_failure.call_args[0]
        assert call_args[1] == 401


@pytest.mark.asyncio
async def test_403_token_invalid_keyword():
    """测试包含invalid关键词的403识别为Token失效"""
    mock_response = Mock()
    mock_response.status_code = 403
    mock_response.text = '{"error":"Invalid credentials"}'
    mock_response.json.return_value = {"error": "Invalid credentials"}

    with patch('app.services.grok.client.token_manager') as mock_tm:
        mock_tm.record_failure = AsyncMock()

        with pytest.raises(Exception):
            await GrokClient._handle_error(mock_response, "sso-rw=test;sso=test")

        call_args = mock_tm.record_failure.call_args[0]
        assert call_args[1] == 401
