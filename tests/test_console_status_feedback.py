"""Console _status_feedback 函数单元测试

测试不同 HTTP 状态码的正确分类：
- 403 → CHALLENGE（触发 CF 求解）
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
