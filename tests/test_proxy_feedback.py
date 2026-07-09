"""Proxy feedback 传输错误检测测试

验证500+状态码中传输错误（curl/HTTP2/connection）的正确分类：
- curl 错误 → TRANSPORT_ERROR（触发代理轮换）
- HTTP/2 stream 错误 → TRANSPORT_ERROR
- connection 错误 → TRANSPORT_ERROR
- 上游服务器错误 → UPSTREAM_5XX（不触发代理轮换）
"""

import pytest
from app.dataplane.reverse.transport._proxy_feedback import (
    upstream_feedback,
    _is_transport_error,
)
from app.platform.errors import UpstreamError
from app.control.proxy.models import ProxyFeedbackKind


class TestTransportErrorDetection:
    """测试 _is_transport_error 函数"""

    def test_curl_error_is_transport(self):
        assert _is_transport_error("Failed to perform, curl: (92)") is True

    def test_http2_stream_is_transport(self):
        assert _is_transport_error("HTTP/2 stream 1 was not closed cleanly") is True

    def test_internal_error_is_transport(self):
        assert _is_transport_error("INTERNAL_ERROR (err 2)") is True

    def test_connection_refused_is_transport(self):
        assert _is_transport_error("connection refused") is True

    def test_connection_reset_is_transport(self):
        assert _is_transport_error("connection reset by peer") is True

    def test_connection_timed_out_is_transport(self):
        assert _is_transport_error("connection timed out") is True

    def test_ssl_error_is_transport(self):
        assert _is_transport_error("SSL certificate problem") is True

    def test_certificate_error_is_transport(self):
        assert _is_transport_error("certificate verify failed") is True

    def test_upstream_error_is_not_transport(self):
        assert _is_transport_error('{"error":"Bad Gateway"}') is False

    def test_empty_body_is_not_transport(self):
        assert _is_transport_error("") is False

    def test_html_error_is_not_transport(self):
        assert _is_transport_error("<html><body>502 Bad Gateway</body></html>") is False


class TestUpstreamFeedbackTransportErrors:
    """测试 upstream_feedback 对传输错误的分类"""

    def test_502_curl_error_returns_transport_error(self):
        exc = UpstreamError(
            "test",
            status=502,
            body="Failed to perform, curl: (92) HTTP/2 stream 1 was not closed cleanly",
        )
        result = upstream_feedback(exc)
        assert result.kind == ProxyFeedbackKind.TRANSPORT_ERROR

    def test_502_connection_refused_returns_transport_error(self):
        exc = UpstreamError("test", status=502, body="connection refused")
        result = upstream_feedback(exc)
        assert result.kind == ProxyFeedbackKind.TRANSPORT_ERROR

    def test_502_upstream_error_returns_upstream_5xx(self):
        exc = UpstreamError("test", status=502, body='{"error":"Bad Gateway"}')
        result = upstream_feedback(exc)
        assert result.kind == ProxyFeedbackKind.UPSTREAM_5XX

    def test_500_empty_body_returns_upstream_5xx(self):
        exc = UpstreamError("test", status=500, body="")
        result = upstream_feedback(exc)
        assert result.kind == ProxyFeedbackKind.UPSTREAM_5XX

    def test_503_connection_reset_returns_transport_error(self):
        exc = UpstreamError("test", status=503, body="connection reset by peer")
        result = upstream_feedback(exc)
        assert result.kind == ProxyFeedbackKind.TRANSPORT_ERROR

    def test_504_timeout_returns_transport_error(self):
        exc = UpstreamError("test", status=504, body="connection timed out")
        result = upstream_feedback(exc)
        assert result.kind == ProxyFeedbackKind.TRANSPORT_ERROR

    def test_429_returns_rate_limited(self):
        exc = UpstreamError("test", status=429, body="rate limited")
        result = upstream_feedback(exc)
        assert result.kind == ProxyFeedbackKind.RATE_LIMITED

    def test_403_cf_challenge_returns_challenge(self):
        exc = UpstreamError(
            "test",
            status=403,
            body="<html><title>Just a moment...</title></html>",
        )
        result = upstream_feedback(exc)
        assert result.kind == ProxyFeedbackKind.CHALLENGE

    def test_403_blocked_user_returns_forbidden(self):
        exc = UpstreamError(
            "test",
            status=403,
            body='{"error":"User is blocked [WKE=unauthorized:blocked-user]"}',
        )
        result = upstream_feedback(exc)
        assert result.kind == ProxyFeedbackKind.FORBIDDEN


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
