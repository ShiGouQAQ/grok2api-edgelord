"""客户端断开检测测试"""

import pytest
import asyncio
from unittest.mock import AsyncMock, Mock, patch, MagicMock
from fastapi import Request
from fastapi.testclient import TestClient
from main import app


class MockRequest:
    """模拟FastAPI Request对象"""
    def __init__(self, disconnected=False):
        self._disconnected = disconnected

    async def is_disconnected(self):
        """模拟客户端断开检测"""
        return self._disconnected


class MockResponse:
    """模拟curl_cffi响应对象"""
    def __init__(self, chunks):
        self.chunks = chunks
        self.status_code = 200
        self._closed = False

    def iter_lines(self):
        """模拟流式响应"""
        for chunk in self.chunks:
            yield chunk

    def close(self):
        """关闭响应"""
        self._closed = True


@pytest.mark.asyncio
async def test_client_disconnect_detection_in_stream():
    """测试流式响应中的客户端断开检测"""
    from app.services.grok.processer import GrokResponseProcessor

    # 创建模拟的断开连接的请求
    mock_request = MockRequest(disconnected=True)

    # 创建模拟响应
    chunks = [
        b'{"result":{"response":{"token":"Hello"}}}',
        b'{"result":{"response":{"token":" World"}}}',
    ]
    mock_response = MockResponse(chunks)

    # 处理流式响应
    result_chunks = []
    async for chunk in GrokResponseProcessor.process_stream(
        mock_response,
        "test_token",
        mock_request
    ):
        result_chunks.append(chunk)

    # 客户端断开后应该立即停止，不应该有任何输出
    assert len(result_chunks) == 0


@pytest.mark.asyncio
async def test_client_connected_stream_continues():
    """测试客户端连接时流式响应正常继续"""
    from app.services.grok.processer import GrokResponseProcessor

    # 创建模拟的连接中的请求
    mock_request = MockRequest(disconnected=False)

    # 创建模拟响应
    chunks = [
        b'{"result":{"response":{"token":"Hello"}}}',
        b'{"result":{"response":{"token":" World"}}}',
    ]
    mock_response = MockResponse(chunks)

    # 处理流式响应
    result_chunks = []
    async for chunk in GrokResponseProcessor.process_stream(
        mock_response,
        "test_token",
        mock_request
    ):
        result_chunks.append(chunk)

    # 客户端连接时应该正常处理所有chunks
    assert len(result_chunks) > 0


@pytest.mark.asyncio
async def test_client_disconnect_during_video_download():
    """测试视频下载前检测到客户端断开"""
    from app.services.grok.processer import GrokResponseProcessor

    # 创建模拟的断开连接的请求
    mock_request = MockRequest(disconnected=True)

    # 创建包含视频响应的模拟数据
    chunks = [
        b'{"result":{"response":{"streamingVideoGenerationResponse":{"progress":100,"videoUrl":"test/video.mp4"}}}}',
    ]
    mock_response = MockResponse(chunks)

    # Mock video_cache_service
    with patch('app.services.grok.processer.video_cache_service') as mock_video_service:
        mock_video_service.download_video = AsyncMock(return_value="/path/to/video")

        result_chunks = []
        async for chunk in GrokResponseProcessor.process_stream(
            mock_response,
            "test_token",
            mock_request
        ):
            result_chunks.append(chunk)

        # 客户端断开后应该停止，不应该调用视频下载
        assert mock_video_service.download_video.call_count == 0


@pytest.mark.asyncio
async def test_client_disconnect_during_image_download():
    """测试图片下载前检测到客户端断开"""
    from app.services.grok.processer import GrokResponseProcessor

    # 创建模拟的断开连接的请求
    mock_request = MockRequest(disconnected=True)

    # 创建包含图片响应的模拟数据
    chunks = [
        b'{"result":{"response":{"imageAttachmentInfo":{},"modelResponse":{"generatedImageUrls":["test/image.jpg"]}}}}',
    ]
    mock_response = MockResponse(chunks)

    # Mock image_cache_service
    with patch('app.services.grok.processer.image_cache_service') as mock_image_service:
        mock_image_service.download_image = AsyncMock(return_value="/path/to/image")

        result_chunks = []
        async for chunk in GrokResponseProcessor.process_stream(
            mock_response,
            "test_token",
            mock_request
        ):
            result_chunks.append(chunk)

        # 客户端断开后应该停止，不应该调用图片下载
        assert mock_image_service.download_image.call_count == 0


@pytest.mark.asyncio
async def test_stream_without_request_object():
    """测试没有Request对象时流式响应正常工作（向后兼容）"""
    from app.services.grok.processer import GrokResponseProcessor

    # 不传递request对象（None）
    chunks = [
        b'{"result":{"response":{"token":"Test"}}}',
    ]
    mock_response = MockResponse(chunks)

    result_chunks = []
    async for chunk in GrokResponseProcessor.process_stream(
        mock_response,
        "test_token",
        None  # 没有request对象
    ):
        result_chunks.append(chunk)

    # 没有request对象时应该正常处理
    assert len(result_chunks) > 0


@pytest.mark.asyncio
async def test_chat_endpoint_passes_request_for_stream():
    """测试聊天端点在流式请求时传递Request对象"""
    from app.services.grok.client import GrokClient

    with patch.object(GrokClient, 'openai_to_grok', new_callable=AsyncMock) as mock_grok:
        mock_grok.return_value = AsyncMock()

        client = TestClient(app)

        # 模拟流式请求
        with patch('app.api.v1.chat.auth_manager.verify', return_value=None):
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "grok-beta",
                    "messages": [{"role": "user", "content": "test"}],
                    "stream": True
                }
            )

        # 验证GrokClient.openai_to_grok被调用
        assert mock_grok.called
        # 验证第二个参数（raw_request）不为None
        call_args = mock_grok.call_args
        assert call_args[0][1] is not None  # raw_request参数


@pytest.mark.asyncio
async def test_chat_endpoint_no_request_for_non_stream():
    """测试聊天端点在非流式请求时不传递Request对象"""
    from app.services.grok.client import GrokClient

    with patch.object(GrokClient, 'openai_to_grok', new_callable=AsyncMock) as mock_grok:
        mock_grok.return_value = {
            "id": "test",
            "choices": [{"message": {"content": "test"}}]
        }

        client = TestClient(app)

        # 模拟非流式请求
        with patch('app.api.v1.chat.auth_manager.verify', return_value=None):
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "grok-beta",
                    "messages": [{"role": "user", "content": "test"}],
                    "stream": False
                }
            )

        # 验证GrokClient.openai_to_grok被调用
        assert mock_grok.called
        # 验证第二个参数（raw_request）为None
        call_args = mock_grok.call_args
        assert call_args[0][1] is None  # raw_request参数应该是None


@pytest.mark.asyncio
async def test_disconnect_detection_exception_handling():
    """测试断开检测异常处理"""
    from app.services.grok.processer import GrokResponseProcessor

    # 创建会抛出异常的mock request
    mock_request = Mock()
    mock_request.is_disconnected = AsyncMock(side_effect=Exception("Connection error"))

    chunks = [
        b'{"result":{"response":{"token":"Test"}}}',
    ]
    mock_response = MockResponse(chunks)

    # 即使is_disconnected抛出异常，也应该继续处理
    result_chunks = []
    async for chunk in GrokResponseProcessor.process_stream(
        mock_response,
        "test_token",
        mock_request
    ):
        result_chunks.append(chunk)

    # 异常被捕获，流式处理继续
    assert len(result_chunks) > 0
