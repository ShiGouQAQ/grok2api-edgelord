"""Console _status_feedback 函数单元测试

测试不同 HTTP 状态码的正确分类：
- 403 → CHALLENGE（触发 CF 求解）
- 403 + body 含账号级标记 → FORBIDDEN（账号问题，非 CF）
- 429 → RATE_LIMITED（触发速率限制）
- >= 500 → UPSTREAM_5XX（触发上游服务器错误）
- 其他 → FORBIDDEN（默认拒绝）
"""

import pytest
from app.dataplane.reverse.protocol.xai_console_chat import _status_feedback
from app.control.proxy.models import ProxyFeedbackKind


class TestStatusFeedback:
    """测试 _status_feedback 函数"""

    # ============================================================
    # 403 状态码 → 应该返回 CHALLENGE
    # ============================================================

    def test_403_returns_challenge(self):
        """测试 403 状态码返回 CHALLENGE"""
        result = _status_feedback(403)
        assert result.kind == ProxyFeedbackKind.CHALLENGE
        assert result.status_code == 403

    # ============================================================
    # 429 状态码 → 应该返回 RATE_LIMITED
    # ============================================================

    def test_429_returns_rate_limited(self):
        """测试 429 状态码返回 RATE_LIMITED"""
        result = _status_feedback(429)
        assert result.kind == ProxyFeedbackKind.RATE_LIMITED
        assert result.status_code == 429

    # ============================================================
    # 500+ 状态码 → 应该返回 UPSTREAM_5XX
    # ============================================================

    def test_500_returns_upstream_5xx(self):
        """测试 500 状态码返回 UPSTREAM_5XX"""
        result = _status_feedback(500)
        assert result.kind == ProxyFeedbackKind.UPSTREAM_5XX
        assert result.status_code == 500

    def test_502_returns_upstream_5xx(self):
        """测试 502 状态码返回 UPSTREAM_5XX"""
        result = _status_feedback(502)
        assert result.kind == ProxyFeedbackKind.UPSTREAM_5XX
        assert result.status_code == 502

    def test_503_returns_upstream_5xx(self):
        """测试 503 状态码返回 UPSTREAM_5XX"""
        result = _status_feedback(503)
        assert result.kind == ProxyFeedbackKind.UPSTREAM_5XX
        assert result.status_code == 503

    def test_504_returns_upstream_5xx(self):
        """测试 504 状态码返回 UPSTREAM_5XX"""
        result = _status_feedback(504)
        assert result.kind == ProxyFeedbackKind.UPSTREAM_5XX
        assert result.status_code == 504

    def test_599_returns_upstream_5xx(self):
        """测试 599 状态码返回 UPSTREAM_5XX（边界值）"""
        result = _status_feedback(599)
        assert result.kind == ProxyFeedbackKind.UPSTREAM_5XX
        assert result.status_code == 599

    # ============================================================
    # 其他状态码 → 应该返回 FORBIDDEN（默认）
    # ============================================================

    def test_400_returns_forbidden(self):
        """测试 400 状态码返回 FORBIDDEN（默认）"""
        result = _status_feedback(400)
        assert result.kind == ProxyFeedbackKind.FORBIDDEN
        assert result.status_code == 400

    def test_401_returns_forbidden(self):
        """测试 401 状态码返回 FORBIDDEN（默认）"""
        result = _status_feedback(401)
        assert result.kind == ProxyFeedbackKind.FORBIDDEN
        assert result.status_code == 401

    def test_404_returns_forbidden(self):
        """测试 404 状态码返回 FORBIDDEN（默认）"""
        result = _status_feedback(404)
        assert result.kind == ProxyFeedbackKind.FORBIDDEN
        assert result.status_code == 404

    def test_405_returns_forbidden(self):
        """测试 405 状态码返回 FORBIDDEN（默认）"""
        result = _status_feedback(405)
        assert result.kind == ProxyFeedbackKind.FORBIDDEN
        assert result.status_code == 405

    def test_408_returns_forbidden(self):
        """测试 408 状态码返回 FORBIDDEN（默认）"""
        result = _status_feedback(408)
        assert result.kind == ProxyFeedbackKind.FORBIDDEN
        assert result.status_code == 408

    def test_499_returns_forbidden(self):
        """测试 499 状态码返回 FORBIDDEN（默认，边界值）"""
        result = _status_feedback(499)
        assert result.kind == ProxyFeedbackKind.FORBIDDEN
        assert result.status_code == 499

    def test_200_returns_forbidden(self):
        """测试 200 状态码返回 FORBIDDEN（默认，非预期状态码）"""
        result = _status_feedback(200)
        assert result.kind == ProxyFeedbackKind.FORBIDDEN
        assert result.status_code == 200

    # ============================================================
    # 边界情况测试
    # ============================================================

    def test_499_boundary_forbidden(self):
        """测试 499 状态码边界（不是 500，应该是 FORBIDDEN）"""
        result = _status_feedback(499)
        assert result.kind == ProxyFeedbackKind.FORBIDDEN
        assert result.status_code == 499

    def test_500_boundary_upstream_5xx(self):
        """测试 500 状态码边界（是 500，应该是 UPSTREAM_5XX）"""
        result = _status_feedback(500)
        assert result.kind == ProxyFeedbackKind.UPSTREAM_5XX
        assert result.status_code == 500

    def test_very_high_status_code(self):
        """测试非常大的状态码（>= 500）"""
        result = _status_feedback(999)
        assert result.kind == ProxyFeedbackKind.UPSTREAM_5XX
        assert result.status_code == 999

    def test_status_code_1(self):
        """测试最小状态码 1"""
        result = _status_feedback(1)
        assert result.kind == ProxyFeedbackKind.FORBIDDEN
        assert result.status_code == 1

    # ============================================================
    # 返回值结构测试
    # ============================================================

    def test_returns_proxy_feedback_with_status_code(self):
        """测试返回的 ProxyFeedback 对象包含正确的 status_code"""
        result = _status_feedback(403)
        assert hasattr(result, "kind")
        assert hasattr(result, "status_code")
        assert result.status_code == 403
        assert result.kind == ProxyFeedbackKind.CHALLENGE

    def test_returns_proxy_feedback_for_429(self):
        """测试 429 返回的 ProxyFeedback 对象"""
        result = _status_feedback(429)
        assert hasattr(result, "kind")
        assert hasattr(result, "status_code")
        assert result.status_code == 429
        assert result.kind == ProxyFeedbackKind.RATE_LIMITED

    def test_returns_proxy_feedback_for_500(self):
        """测试 500 返回的 ProxyFeedback 对象"""
        result = _status_feedback(500)
        assert hasattr(result, "kind")
        assert hasattr(result, "status_code")
        assert result.status_code == 500
        assert result.kind == ProxyFeedbackKind.UPSTREAM_5XX

    def test_returns_proxy_feedback_for_other(self):
        """测试其他状态码返回的 ProxyFeedback 对象"""
        result = _status_feedback(404)
        assert hasattr(result, "kind")
        assert hasattr(result, "status_code")
        assert result.status_code == 404
        assert result.kind == ProxyFeedbackKind.FORBIDDEN


class TestStatusFeedbackEdgeCases:
    """测试边界情况"""

    def test_all_four_classification_types(self):
        """测试四种分类类型都能正确返回"""
        test_cases = [
            (403, ProxyFeedbackKind.CHALLENGE),
            (429, ProxyFeedbackKind.RATE_LIMITED),
            (500, ProxyFeedbackKind.UPSTREAM_5XX),
            (502, ProxyFeedbackKind.UPSTREAM_5XX),
            (503, ProxyFeedbackKind.UPSTREAM_5XX),
            (400, ProxyFeedbackKind.FORBIDDEN),
            (401, ProxyFeedbackKind.FORBIDDEN),
            (404, ProxyFeedbackKind.FORBIDDEN),
            (405, ProxyFeedbackKind.FORBIDDEN),
        ]

        for status, expected_kind in test_cases:
            result = _status_feedback(status)
            assert result.kind == expected_kind, (
                f"Status {status} should return {expected_kind}, got {result.kind}"
            )
            assert result.status_code == status, (
                f"Status code should be {status}, got {result.status_code}"
            )

    def test_status_code_range_500_to_599(self):
        """测试 500-599 状态码范围都是 UPSTREAM_5XX"""
        for status in range(500, 600):
            result = _status_feedback(status)
            assert result.kind == ProxyFeedbackKind.UPSTREAM_5XX, (
                f"Status {status} should be UPSTREAM_5XX"
            )
            assert result.status_code == status

    def test_status_code_range_400_to_499_except_403_429(self):
        """测试 400-499 状态码范围（除了 403 和 429）都是 FORBIDDEN"""
        for status in range(400, 500):
            if status in (403, 429):
                continue
            result = _status_feedback(status)
            assert result.kind == ProxyFeedbackKind.FORBIDDEN, (
                f"Status {status} should be FORBIDDEN"
            )
            assert result.status_code == status


class TestStatusFeedbackBodyCheck:
    """测试 403 + body 区分账号级错误和 CF 挑战"""

    # ============================================================
    # 账号级 403 → FORBIDDEN（非 CF 挑战）
    # ============================================================

    def test_403_blocked_user_returns_forbidden(self):
        """blocked-user 应返回 FORBIDDEN，不是 CHALLENGE"""
        body = '{"error":"User is blocked [WKE=unauthorized:blocked-user]"}'
        result = _status_feedback(403, body)
        assert result.kind == ProxyFeedbackKind.FORBIDDEN
        assert result.status_code == 403

    def test_403_email_domain_rejected_returns_forbidden(self):
        """email-domain-rejected 应返回 FORBIDDEN"""
        body = (
            "This email domain has been rejected. [WKE=account:email-domain-rejected]"
        )
        result = _status_feedback(403, body)
        assert result.kind == ProxyFeedbackKind.FORBIDDEN
        assert result.status_code == 403

    def test_403_invalid_credentials_returns_forbidden(self):
        """invalid-credentials 应返回 FORBIDDEN"""
        body = '{"code":"invalid-credentials","error":"Invalid credentials"}'
        result = _status_feedback(403, body)
        assert result.kind == ProxyFeedbackKind.FORBIDDEN
        assert result.status_code == 403

    def test_403_bad_credentials_returns_forbidden(self):
        """bad-credentials 应返回 FORBIDDEN"""
        body = '{"error":"bad-credentials"}'
        result = _status_feedback(403, body)
        assert result.kind == ProxyFeedbackKind.FORBIDDEN
        assert result.status_code == 403

    def test_403_session_expired_returns_forbidden(self):
        """session-expired 应返回 FORBIDDEN"""
        body = '{"error":"session-expired"}'
        result = _status_feedback(403, body)
        assert result.kind == ProxyFeedbackKind.FORBIDDEN
        assert result.status_code == 403

    def test_403_account_suspended_returns_forbidden(self):
        """account suspended 应返回 FORBIDDEN"""
        body = '{"error":"account suspended"}'
        result = _status_feedback(403, body)
        assert result.kind == ProxyFeedbackKind.FORBIDDEN
        assert result.status_code == 403

    # ============================================================
    # CF 挑战 403 → CHALLENGE（无 body 或非账号级）
    # ============================================================

    def test_403_empty_body_returns_challenge(self):
        """空 body 的 403 应返回 CHALLENGE"""
        result = _status_feedback(403, "")
        assert result.kind == ProxyFeedbackKind.CHALLENGE

    def test_403_no_body_returns_challenge(self):
        """不传 body 的 403 应返回 CHALLENGE（向后兼容）"""
        result = _status_feedback(403)
        assert result.kind == ProxyFeedbackKind.CHALLENGE

    def test_403_cf_challenge_returns_challenge(self):
        """CF 挑战页应返回 CHALLENGE"""
        body = (
            "<!DOCTYPE html><html><head><title>Just a moment...</title></head></html>"
        )
        result = _status_feedback(403, body)
        assert result.kind == ProxyFeedbackKind.CHALLENGE

    def test_403_random_body_returns_challenge(self):
        """无关 body 的 403 应返回 CHALLENGE"""
        body = '{"error":"some other error"}'
        result = _status_feedback(403, body)
        assert result.kind == ProxyFeedbackKind.CHALLENGE

    # ============================================================
    # 非 403 状态码不受 body 影响
    # ============================================================

    def test_429_with_body_still_rate_limited(self):
        """429 即使有 body 也应返回 RATE_LIMITED"""
        body = '{"error":"rate limited"}'
        result = _status_feedback(429, body)
        assert result.kind == ProxyFeedbackKind.RATE_LIMITED

    def test_500_with_body_still_upstream_5xx(self):
        """500 即使有 body 也应返回 UPSTREAM_5XX"""
        body = '{"error":"internal server error"}'
        result = _status_feedback(500, body)
        assert result.kind == ProxyFeedbackKind.UPSTREAM_5XX

    # ============================================================
    # 大小写不敏感
    # ============================================================

    def test_403_blocked_user_case_insensitive(self):
        """body 检查应大小写不敏感"""
        body = '{"error":"User Is BLOCKED [WKE=unauthorized:BLOCKED-USER]"}'
        result = _status_feedback(403, body)
        assert result.kind == ProxyFeedbackKind.FORBIDDEN

    def test_403_email_domain_rejected_case_insensitive(self):
        """body 检查应大小写不敏感"""
        body = "EMAIL-DOMAIN-REJECTED"
        result = _status_feedback(403, body)
        assert result.kind == ProxyFeedbackKind.FORBIDDEN

    # ============================================================
    # 500+ 状态码 + transport error body → TRANSPORT_ERROR
    # ============================================================

    def test_502_curl_error_returns_transport_error(self):
        """502 + curl 错误应返回 TRANSPORT_ERROR"""
        body = "Failed to perform, curl: (92) HTTP/2 stream 1 was not closed cleanly: INTERNAL_ERROR"
        result = _status_feedback(502, body)
        assert result.kind == ProxyFeedbackKind.TRANSPORT_ERROR

    def test_502_http2_stream_returns_transport_error(self):
        """502 + HTTP/2 stream 错误应返回 TRANSPORT_ERROR"""
        body = "HTTP/2 stream 1 was not closed cleanly"
        result = _status_feedback(502, body)
        assert result.kind == ProxyFeedbackKind.TRANSPORT_ERROR

    def test_502_connection_refused_returns_transport_error(self):
        """502 + connection refused 应返回 TRANSPORT_ERROR"""
        body = "connection refused"
        result = _status_feedback(502, body)
        assert result.kind == ProxyFeedbackKind.TRANSPORT_ERROR

    def test_502_ssl_error_returns_transport_error(self):
        """502 + SSL 错误应返回 TRANSPORT_ERROR"""
        body = "SSL certificate problem"
        result = _status_feedback(502, body)
        assert result.kind == ProxyFeedbackKind.TRANSPORT_ERROR

    def test_502_upstream_error_returns_upstream_5xx(self):
        """502 + 非 transport 错误应返回 UPSTREAM_5XX"""
        body = '{"error":"Bad Gateway"}'
        result = _status_feedback(502, body)
        assert result.kind == ProxyFeedbackKind.UPSTREAM_5XX

    def test_500_empty_body_returns_upstream_5xx(self):
        """500 + 空 body 应返回 UPSTREAM_5XX"""
        result = _status_feedback(500, "")
        assert result.kind == ProxyFeedbackKind.UPSTREAM_5XX

    def test_500_no_body_returns_upstream_5xx(self):
        """500 + 无 body 应返回 UPSTREAM_5XX"""
        result = _status_feedback(500)
        assert result.kind == ProxyFeedbackKind.UPSTREAM_5XX

    def test_503_connection_reset_returns_transport_error(self):
        """503 + connection reset 应返回 TRANSPORT_ERROR"""
        body = "connection reset by peer"
        result = _status_feedback(503, body)
        assert result.kind == ProxyFeedbackKind.TRANSPORT_ERROR

    def test_504_timeout_returns_transport_error(self):
        """504 + timeout 应返回 TRANSPORT_ERROR"""
        body = "connection timed out"
        result = _status_feedback(504, body)
        assert result.kind == ProxyFeedbackKind.TRANSPORT_ERROR


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
