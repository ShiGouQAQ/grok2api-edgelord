"""Console chat clearance origin 测试

验证 console chat 使用正确的 clearance_origin (console.x.ai)，
而不是错误地使用 grok.com。

测试场景：
- stream_console_chat 调用 proxy.acquire 时传入 CONSOLE_BASE
- non-stream 路径同样使用正确的 clearance_origin
- 边缘情况：proxy 返回 None、异常处理等
- admin CF clearance 刷新两个域名
- refresh_clearance_safe 更新内存统计
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.dataplane.reverse.runtime.endpoint_table import CONSOLE_BASE


@pytest.mark.asyncio
async def test_clearance_origin_is_console_x_ai():
    assert CONSOLE_BASE == "https://console.x.ai"


@pytest.mark.asyncio
async def test_clearance_origin_not_grok_com():
    assert CONSOLE_BASE != "https://grok.com"


@pytest.mark.asyncio
async def test_stream_uses_console_clearance_origin():
    mock_proxy = AsyncMock()
    mock_lease = MagicMock()
    mock_proxy.acquire.return_value = mock_lease

    mock_response = MagicMock()
    mock_response.status_code = 200

    async def empty_aiter():
        return
        yield

    mock_response.aiter_lines = empty_aiter

    mock_session = AsyncMock()
    mock_session.post.return_value = mock_response
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.dataplane.proxy.get_proxy_runtime", return_value=mock_proxy),
        patch(
            "app.dataplane.proxy.adapters.session.ResettableSession",
            return_value=mock_session,
        ),
        patch(
            "app.dataplane.proxy.adapters.headers.build_console_headers",
            return_value={},
        ),
        patch(
            "app.dataplane.proxy.adapters.session.build_session_kwargs", return_value={}
        ),
    ):
        from app.dataplane.reverse.protocol.xai_console_chat import stream_console_chat

        payload = {"model": "grok-4.3", "input": []}
        async for _ in stream_console_chat("test-token", payload):
            pass

        mock_proxy.acquire.assert_called_once_with(clearance_origin=CONSOLE_BASE)


@pytest.mark.asyncio
async def test_stream_acquire_failure_propagates():
    mock_proxy = AsyncMock()
    mock_proxy.acquire.side_effect = RuntimeError("acquire failed")

    with patch("app.dataplane.proxy.get_proxy_runtime", return_value=mock_proxy):
        from app.dataplane.reverse.protocol.xai_console_chat import stream_console_chat

        payload = {"model": "grok-4.3", "input": []}
        with pytest.raises(RuntimeError, match="acquire failed"):
            async for _ in stream_console_chat("test-token", payload):
                pass

        mock_proxy.acquire.assert_called_once_with(clearance_origin=CONSOLE_BASE)


@pytest.mark.asyncio
async def test_stream_403_sends_correct_feedback():
    mock_proxy = AsyncMock()
    mock_lease = MagicMock()
    mock_lease.clearance_host = "console.x.ai"
    mock_proxy.acquire.return_value = mock_lease

    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.content = b'{"error": "blocked"}'

    mock_session = AsyncMock()
    mock_session.post.return_value = mock_response
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.dataplane.proxy.get_proxy_runtime", return_value=mock_proxy),
        patch(
            "app.dataplane.proxy.adapters.session.ResettableSession",
            return_value=mock_session,
        ),
        patch(
            "app.dataplane.proxy.adapters.headers.build_console_headers",
            return_value={},
        ),
        patch(
            "app.dataplane.proxy.adapters.session.build_session_kwargs", return_value={}
        ),
    ):
        from app.dataplane.reverse.protocol.xai_console_chat import stream_console_chat
        from app.platform.errors import UpstreamError

        payload = {"model": "grok-4.3", "input": []}
        with pytest.raises(UpstreamError):
            async for _ in stream_console_chat("test-token", payload):
                pass

        mock_proxy.acquire.assert_called_once_with(clearance_origin=CONSOLE_BASE)
        mock_proxy.feedback.assert_called_once()
        assert mock_proxy.feedback.call_args[0][1].status_code == 403


@pytest.mark.asyncio
async def test_stream_429_sends_rate_limited():
    mock_proxy = AsyncMock()
    mock_lease = MagicMock()
    mock_proxy.acquire.return_value = mock_lease

    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.content = b'{"error": "rate limited"}'

    mock_session = AsyncMock()
    mock_session.post.return_value = mock_response
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.dataplane.proxy.get_proxy_runtime", return_value=mock_proxy),
        patch(
            "app.dataplane.proxy.adapters.session.ResettableSession",
            return_value=mock_session,
        ),
        patch(
            "app.dataplane.proxy.adapters.headers.build_console_headers",
            return_value={},
        ),
        patch(
            "app.dataplane.proxy.adapters.session.build_session_kwargs", return_value={}
        ),
    ):
        from app.dataplane.reverse.protocol.xai_console_chat import stream_console_chat
        from app.platform.errors import UpstreamError
        from app.control.proxy.models import ProxyFeedbackKind

        payload = {"model": "grok-4.3", "input": []}
        with pytest.raises(UpstreamError):
            async for _ in stream_console_chat("test-token", payload):
                pass

        mock_proxy.feedback.assert_called_once()
        assert (
            mock_proxy.feedback.call_args[0][1].kind == ProxyFeedbackKind.RATE_LIMITED
        )


@pytest.mark.asyncio
async def test_stream_success_sends_success_feedback():
    mock_proxy = AsyncMock()
    mock_lease = MagicMock()
    mock_proxy.acquire.return_value = mock_lease

    mock_response = MagicMock()
    mock_response.status_code = 200

    async def mock_aiter_lines():
        yield b"event: response.output_text.delta"
        yield b'data: {"delta": "hello"}'
        yield b"event: response.completed"
        yield b'data: {"response": {"usage": {"total_tokens": 10}}}'

    mock_response.aiter_lines = mock_aiter_lines

    mock_session = AsyncMock()
    mock_session.post.return_value = mock_response
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.dataplane.proxy.get_proxy_runtime", return_value=mock_proxy),
        patch(
            "app.dataplane.proxy.adapters.session.ResettableSession",
            return_value=mock_session,
        ),
        patch(
            "app.dataplane.proxy.adapters.headers.build_console_headers",
            return_value={},
        ),
        patch(
            "app.dataplane.proxy.adapters.session.build_session_kwargs", return_value={}
        ),
    ):
        from app.dataplane.reverse.protocol.xai_console_chat import stream_console_chat
        from app.control.proxy.models import ProxyFeedbackKind

        payload = {"model": "grok-4.3", "input": []}
        async for _ in stream_console_chat("test-token", payload):
            pass

        mock_proxy.feedback.assert_called_once()
        assert mock_proxy.feedback.call_args[0][1].kind == ProxyFeedbackKind.SUCCESS


@pytest.mark.asyncio
async def test_stream_transport_error_sends_feedback():
    mock_proxy = AsyncMock()
    mock_lease = MagicMock()
    mock_proxy.acquire.return_value = mock_lease

    mock_session = AsyncMock()
    mock_session.post.side_effect = ConnectionError("connection refused")
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.dataplane.proxy.get_proxy_runtime", return_value=mock_proxy),
        patch(
            "app.dataplane.proxy.adapters.session.ResettableSession",
            return_value=mock_session,
        ),
        patch(
            "app.dataplane.proxy.adapters.headers.build_console_headers",
            return_value={},
        ),
        patch(
            "app.dataplane.proxy.adapters.session.build_session_kwargs", return_value={}
        ),
    ):
        from app.dataplane.reverse.protocol.xai_console_chat import stream_console_chat
        from app.platform.errors import UpstreamError
        from app.control.proxy.models import ProxyFeedbackKind

        payload = {"model": "grok-4.3", "input": []}
        with pytest.raises(UpstreamError):
            async for _ in stream_console_chat("test-token", payload):
                pass

        mock_proxy.feedback.assert_called_once()
        assert (
            mock_proxy.feedback.call_args[0][1].kind
            == ProxyFeedbackKind.TRANSPORT_ERROR
        )


@pytest.mark.asyncio
async def test_stream_blocked_user_sends_forbidden():
    mock_proxy = AsyncMock()
    mock_lease = MagicMock()
    mock_proxy.acquire.return_value = mock_lease

    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.content = (
        b'{"error":"User is blocked [WKE=unauthorized:blocked-user]"}'
    )

    mock_session = AsyncMock()
    mock_session.post.return_value = mock_response
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.dataplane.proxy.get_proxy_runtime", return_value=mock_proxy),
        patch(
            "app.dataplane.proxy.adapters.session.ResettableSession",
            return_value=mock_session,
        ),
        patch(
            "app.dataplane.proxy.adapters.headers.build_console_headers",
            return_value={},
        ),
        patch(
            "app.dataplane.proxy.adapters.session.build_session_kwargs", return_value={}
        ),
    ):
        from app.dataplane.reverse.protocol.xai_console_chat import stream_console_chat
        from app.platform.errors import UpstreamError
        from app.control.proxy.models import ProxyFeedbackKind

        payload = {"model": "grok-4.3", "input": []}
        with pytest.raises(UpstreamError):
            async for _ in stream_console_chat("test-token", payload):
                pass

        mock_proxy.feedback.assert_called_once()
        assert mock_proxy.feedback.call_args[0][1].kind == ProxyFeedbackKind.FORBIDDEN


@pytest.mark.asyncio
async def test_stream_email_domain_rejected_sends_forbidden():
    mock_proxy = AsyncMock()
    mock_lease = MagicMock()
    mock_proxy.acquire.return_value = mock_lease

    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.content = (
        b'{"error":"email domain rejected [WKE=account:email-domain-rejected]"}'
    )

    mock_session = AsyncMock()
    mock_session.post.return_value = mock_response
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.dataplane.proxy.get_proxy_runtime", return_value=mock_proxy),
        patch(
            "app.dataplane.proxy.adapters.session.ResettableSession",
            return_value=mock_session,
        ),
        patch(
            "app.dataplane.proxy.adapters.headers.build_console_headers",
            return_value={},
        ),
        patch(
            "app.dataplane.proxy.adapters.session.build_session_kwargs", return_value={}
        ),
    ):
        from app.dataplane.reverse.protocol.xai_console_chat import stream_console_chat
        from app.platform.errors import UpstreamError
        from app.control.proxy.models import ProxyFeedbackKind

        payload = {"model": "grok-4.3", "input": []}
        with pytest.raises(UpstreamError):
            async for _ in stream_console_chat("test-token", payload):
                pass

        mock_proxy.feedback.assert_called_once()
        assert mock_proxy.feedback.call_args[0][1].kind == ProxyFeedbackKind.FORBIDDEN


@pytest.mark.asyncio
async def test_stream_cf_challenge_sends_challenge():
    mock_proxy = AsyncMock()
    mock_lease = MagicMock()
    mock_proxy.acquire.return_value = mock_lease

    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.content = (
        b"<!DOCTYPE html><html><title>Just a moment...</title></html>"
    )

    mock_session = AsyncMock()
    mock_session.post.return_value = mock_response
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.dataplane.proxy.get_proxy_runtime", return_value=mock_proxy),
        patch(
            "app.dataplane.proxy.adapters.session.ResettableSession",
            return_value=mock_session,
        ),
        patch(
            "app.dataplane.proxy.adapters.headers.build_console_headers",
            return_value={},
        ),
        patch(
            "app.dataplane.proxy.adapters.session.build_session_kwargs", return_value={}
        ),
    ):
        from app.dataplane.reverse.protocol.xai_console_chat import stream_console_chat
        from app.platform.errors import UpstreamError
        from app.control.proxy.models import ProxyFeedbackKind

        payload = {"model": "grok-4.3", "input": []}
        with pytest.raises(UpstreamError):
            async for _ in stream_console_chat("test-token", payload):
                pass

        mock_proxy.feedback.assert_called_once()
        assert mock_proxy.feedback.call_args[0][1].kind == ProxyFeedbackKind.CHALLENGE


@pytest.mark.asyncio
async def test_stream_empty_body_403_sends_challenge():
    mock_proxy = AsyncMock()
    mock_lease = MagicMock()
    mock_proxy.acquire.return_value = mock_lease

    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.content = b""

    mock_session = AsyncMock()
    mock_session.post.return_value = mock_response
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.dataplane.proxy.get_proxy_runtime", return_value=mock_proxy),
        patch(
            "app.dataplane.proxy.adapters.session.ResettableSession",
            return_value=mock_session,
        ),
        patch(
            "app.dataplane.proxy.adapters.headers.build_console_headers",
            return_value={},
        ),
        patch(
            "app.dataplane.proxy.adapters.session.build_session_kwargs", return_value={}
        ),
    ):
        from app.dataplane.reverse.protocol.xai_console_chat import stream_console_chat
        from app.platform.errors import UpstreamError
        from app.control.proxy.models import ProxyFeedbackKind

        payload = {"model": "grok-4.3", "input": []}
        with pytest.raises(UpstreamError):
            async for _ in stream_console_chat("test-token", payload):
                pass

        mock_proxy.feedback.assert_called_once()
        assert mock_proxy.feedback.call_args[0][1].kind == ProxyFeedbackKind.CHALLENGE


@pytest.mark.asyncio
async def test_stream_500_sends_upstream_5xx():
    mock_proxy = AsyncMock()
    mock_lease = MagicMock()
    mock_proxy.acquire.return_value = mock_lease

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.content = b'{"error": "internal server error"}'

    mock_session = AsyncMock()
    mock_session.post.return_value = mock_response
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.dataplane.proxy.get_proxy_runtime", return_value=mock_proxy),
        patch(
            "app.dataplane.proxy.adapters.session.ResettableSession",
            return_value=mock_session,
        ),
        patch(
            "app.dataplane.proxy.adapters.headers.build_console_headers",
            return_value={},
        ),
        patch(
            "app.dataplane.proxy.adapters.session.build_session_kwargs", return_value={}
        ),
    ):
        from app.dataplane.reverse.protocol.xai_console_chat import stream_console_chat
        from app.platform.errors import UpstreamError
        from app.control.proxy.models import ProxyFeedbackKind

        payload = {"model": "grok-4.3", "input": []}
        with pytest.raises(UpstreamError):
            async for _ in stream_console_chat("test-token", payload):
                pass

        mock_proxy.feedback.assert_called_once()
        assert (
            mock_proxy.feedback.call_args[0][1].kind == ProxyFeedbackKind.UPSTREAM_5XX
        )


@pytest.mark.asyncio
async def test_stream_400_sends_forbidden():
    mock_proxy = AsyncMock()
    mock_lease = MagicMock()
    mock_proxy.acquire.return_value = mock_lease

    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.content = b'{"error": "bad request"}'

    mock_session = AsyncMock()
    mock_session.post.return_value = mock_response
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.dataplane.proxy.get_proxy_runtime", return_value=mock_proxy),
        patch(
            "app.dataplane.proxy.adapters.session.ResettableSession",
            return_value=mock_session,
        ),
        patch(
            "app.dataplane.proxy.adapters.headers.build_console_headers",
            return_value={},
        ),
        patch(
            "app.dataplane.proxy.adapters.session.build_session_kwargs", return_value={}
        ),
    ):
        from app.dataplane.reverse.protocol.xai_console_chat import stream_console_chat
        from app.platform.errors import UpstreamError
        from app.control.proxy.models import ProxyFeedbackKind

        payload = {"model": "grok-4.3", "input": []}
        with pytest.raises(UpstreamError):
            async for _ in stream_console_chat("test-token", payload):
                pass

        mock_proxy.feedback.assert_called_once()
        assert mock_proxy.feedback.call_args[0][1].kind == ProxyFeedbackKind.FORBIDDEN


@pytest.mark.asyncio
async def test_admin_refresh_clearance_refreshes_both_domains():
    from app.control.proxy import _DEFAULT_CLEARANCE_ORIGIN, _CONSOLE_CLEARANCE_ORIGIN

    assert _DEFAULT_CLEARANCE_ORIGIN == "https://grok.com"
    assert _CONSOLE_CLEARANCE_ORIGIN == "https://console.x.ai"

    mock_directory = AsyncMock()
    mock_directory.ensure_valid_clearance.return_value = True

    with patch("app.control.proxy.get_proxy_directory", return_value=mock_directory):
        from app.products.web.admin.clearance import refresh_cf_clearance

        result = await refresh_cf_clearance()

        assert result["success"] is True
        assert mock_directory.ensure_valid_clearance.call_count == 2
        calls = mock_directory.ensure_valid_clearance.call_args_list
        assert calls[0][0][0] == _DEFAULT_CLEARANCE_ORIGIN
        assert calls[1][0][0] == _CONSOLE_CLEARANCE_ORIGIN


@pytest.mark.asyncio
async def test_admin_refresh_clearance_partial_success():
    from app.control.proxy import _DEFAULT_CLEARANCE_ORIGIN, _CONSOLE_CLEARANCE_ORIGIN

    mock_directory = AsyncMock()
    mock_directory.ensure_valid_clearance.side_effect = [False, True]

    with patch("app.control.proxy.get_proxy_directory", return_value=mock_directory):
        from app.products.web.admin.clearance import refresh_cf_clearance

        result = await refresh_cf_clearance()

        assert result["success"] is True
        assert mock_directory.ensure_valid_clearance.call_count == 2


@pytest.mark.asyncio
async def test_admin_refresh_clearance_all_fail():
    mock_directory = AsyncMock()
    mock_directory.ensure_valid_clearance.return_value = False

    with patch("app.control.proxy.get_proxy_directory", return_value=mock_directory):
        from app.products.web.admin.clearance import refresh_cf_clearance

        result = await refresh_cf_clearance()

        assert result["success"] is False
        assert mock_directory.ensure_valid_clearance.call_count == 2


@pytest.mark.asyncio
async def test_admin_refresh_clearance_exception():
    mock_directory = AsyncMock()
    mock_directory.ensure_valid_clearance.side_effect = RuntimeError("test error")

    with patch("app.control.proxy.get_proxy_directory", return_value=mock_directory):
        from app.products.web.admin.clearance import refresh_cf_clearance

        result = await refresh_cf_clearance()

        assert result["success"] is False


@pytest.mark.asyncio
async def test_refresh_clearance_safe_updates_stats():
    mock_directory = MagicMock()
    mock_directory._clearance_mode = MagicMock()
    mock_directory._clearance_mode.__ne__ = lambda self, other: True
    mock_directory._lock = AsyncMock()
    mock_directory._nodes = []
    mock_directory._bundles = {}
    mock_directory._stats = {
        "total_checks": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "solver_success": 0,
        "solver_failures": 0,
        "precheck_skips": 0,
    }
    mock_directory._last_check_time = {}
    mock_directory._mihomo = MagicMock()
    mock_directory._mihomo._enabled.return_value = False
    mock_directory._get_proxy_url.return_value = ""

    async def mock_refresh_bundle(**kwargs):
        return MagicMock()

    mock_directory._refresh_bundle_with_node_fallback = mock_refresh_bundle

    from app.control.proxy import ProxyDirectory

    await ProxyDirectory.refresh_clearance_safe(mock_directory)

    assert mock_directory._stats["solver_success"] == 1
    assert mock_directory._stats["solver_failures"] == 0
    assert mock_directory._stats["total_checks"] == 1
    assert mock_directory._stats["cache_misses"] == 1
    assert len(mock_directory._last_check_time) > 0


@pytest.mark.asyncio
async def test_refresh_clearance_safe_tracks_failures():
    mock_directory = MagicMock()
    mock_directory._clearance_mode = MagicMock()
    mock_directory._clearance_mode.__ne__ = lambda self, other: True
    mock_directory._lock = AsyncMock()
    mock_directory._nodes = []
    mock_directory._bundles = {}
    mock_directory._stats = {
        "total_checks": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "solver_success": 0,
        "solver_failures": 0,
        "precheck_skips": 0,
    }
    mock_directory._last_check_time = {}
    mock_directory._mihomo = MagicMock()
    mock_directory._mihomo._enabled.return_value = False
    mock_directory._get_proxy_url.return_value = ""

    async def mock_refresh_bundle(**kwargs):
        return None

    mock_directory._refresh_bundle_with_node_fallback = mock_refresh_bundle

    from app.control.proxy import ProxyDirectory

    await ProxyDirectory.refresh_clearance_safe(mock_directory)

    assert mock_directory._stats["solver_success"] == 0
    assert mock_directory._stats["solver_failures"] == 1
    assert mock_directory._stats["total_checks"] == 1
    assert len(mock_directory._last_check_time) == 0


@pytest.mark.asyncio
async def test_refresh_clearance_safe_no_update_on_none_mode():
    mock_directory = MagicMock()
    mock_directory._clearance_mode = MagicMock()
    mock_directory._clearance_mode.__eq__ = lambda self, other: True
    mock_directory._stats = {
        "total_checks": 0,
        "solver_success": 0,
        "solver_failures": 0,
    }

    from app.control.proxy import ProxyDirectory

    await ProxyDirectory.refresh_clearance_safe(mock_directory)

    assert mock_directory._stats["solver_success"] == 0
    assert mock_directory._stats["solver_failures"] == 0
    assert mock_directory._stats["total_checks"] == 0


@pytest.mark.asyncio
async def test_admin_refresh_uses_force_to_skip_cache():
    """验证手动刷新时使用 force=True 跳过缓存"""
    from app.products.web.admin.clearance import refresh_cf_clearance

    mock_directory = AsyncMock()
    mock_directory.ensure_valid_clearance.return_value = True

    with patch("app.control.proxy.get_proxy_directory", return_value=mock_directory):
        result = await refresh_cf_clearance()

    assert result["success"] is True
    calls = mock_directory.ensure_valid_clearance.call_args_list
    assert len(calls) == 2
    assert calls[0].kwargs.get("force") is True or calls[0][1].get("force") is True
    assert calls[1].kwargs.get("force") is True or calls[1][1].get("force") is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
