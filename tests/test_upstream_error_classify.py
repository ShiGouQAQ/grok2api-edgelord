"""_classify_upstream_status 函数单元测试

测试上游错误状态码的核心分类引擎，覆盖：
- 401 → 凭证拒绝
- 402 → 配额耗尽
- 403 → 永久拒绝 / 免费配额 / 消费限制 / 凭证 / 关键词兜底 / 未分类
- 429 → 配额 + 限速
- 其他状态码 → 无标记
- fingerprint 格式化
"""

import pytest

from app.platform.errors import _classify_upstream_status


class TestClassifyUpstream401:
    """测试 401 状态码分类"""

    def test_401_sets_credential_rejected(self):
        """401 → credential_rejected=True, account_scoped=True"""
        fp, kw = _classify_upstream_status(401, "", "", "")
        assert kw["credential_rejected"] is True
        assert kw["account_scoped"] is True
        assert kw["quota_exhausted"] is False
        assert kw["permanent_account_denial"] is False


class TestClassifyUpstream402:
    """测试 402 状态码分类"""

    def test_402_sets_quota_exhausted(self):
        """402 → quota_exhausted=True, account_scoped=True"""
        fp, kw = _classify_upstream_status(402, "", "", "")
        assert kw["quota_exhausted"] is True
        assert kw["account_scoped"] is True
        assert kw["credential_rejected"] is False


class TestClassifyUpstream403:
    """测试 403 状态码分类 — 最复杂的分支"""

    # --- 永久拒绝 ---

    def test_chat_endpoint_denied(self):
        """消息含 'access to the chat endpoint is denied' → permanent_account_denial"""
        _, kw = _classify_upstream_status(
            403, "", "", "access to the chat endpoint is denied"
        )
        assert kw["permanent_account_denial"] is True
        assert kw["account_scoped"] is True

    def test_access_denied_exact_match(self):
        """text.strip() 恰好等于 'access denied' → permanent_account_denial"""
        _, kw = _classify_upstream_status(403, "access denied", "", "")
        assert kw["permanent_account_denial"] is True

    def test_access_denied_with_trailing_whitespace(self):
        """'access denied' 带尾部空白 → strip 后匹配"""
        _, kw = _classify_upstream_status(403, "access denied  ", "", "")
        assert kw["permanent_account_denial"] is True

    def test_access_denied_case_insensitive(self):
        """'Access Denied' 大小写不同 → 仍匹配（text 已 .lower()）"""
        _, kw = _classify_upstream_status(403, "Access Denied", "", "")
        assert kw["permanent_account_denial"] is True

    # --- 免费配额 ---

    def test_free_usage_exhausted_sets_model_quota(self):
        """'used all the included free usage for model' → model + free quota"""
        _, kw = _classify_upstream_status(
            403, "", "", "used all the included free usage for model grok-3"
        )
        assert kw["model_quota_exhausted"] is True
        assert kw["free_quota_exhausted"] is True
        assert kw["quota_exhausted"] is True
        assert kw["account_scoped"] is True

    def test_subscription_free_usage_exhausted(self):
        """'subscription:free-usage-exhausted' → free_quota_exhausted"""
        _, kw = _classify_upstream_status(
            403, "subscription:free-usage-exhausted", "", ""
        )
        assert kw["free_quota_exhausted"] is True
        assert kw["quota_exhausted"] is True

    # --- 消费限制 ---

    def test_spending_limit(self):
        """'personal-team-blocked:spending-limit' → quota_exhausted"""
        _, kw = _classify_upstream_status(
            403, "", "personal-team-blocked:spending-limit", ""
        )
        assert kw["quota_exhausted"] is True

    # --- 凭证类（quota_exhausted 未设置时） ---

    @pytest.mark.parametrize(
        "msg",
        [
            "invalid-credentials",
            "bad-credentials",
            "blocked-user",
            "email-domain-rejected",
            "session not found",
            "session-expired",
            "account suspended",
            "token revoked",
            "token expired",
            "invalid token",
            "unauthorized",
            "authentication",
        ],
    )
    def test_credential_patterns(self, msg):
        """各类凭证关键词 → credential_rejected=True"""
        _, kw = _classify_upstream_status(403, "", "", msg)
        assert kw["credential_rejected"] is True, f"failed for: {msg}"

    # --- 关键词兜底 account_scoped ---

    def test_quota_keyword_sets_account_scoped(self):
        """消息含 'quota' → account_scoped=True"""
        _, kw = _classify_upstream_status(403, "", "", "quota exceeded")
        assert kw["account_scoped"] is True

    def test_billing_keyword_sets_account_scoped(self):
        """消息含 'billing' → account_scoped=True"""
        _, kw = _classify_upstream_status(403, "", "", "billing error")
        assert kw["account_scoped"] is True

    def test_permission_keyword_sets_account_scoped(self):
        """消息含 'permission' → account_scoped=True"""
        _, kw = _classify_upstream_status(403, "", "", "permission denied")
        assert kw["account_scoped"] is True

    def test_usage_exhausted_keyword_sets_account_scoped(self):
        """消息含 'usage-exhausted' → account_scoped=True"""
        _, kw = _classify_upstream_status(403, "", "", "usage-exhausted")
        assert kw["account_scoped"] is True

    # --- 优先级测试：quota 优先于 credential ---

    def test_quota_takes_priority_over_credential(self):
        """同时匹配 free-usage 和 blocked-user 时，quota 优先，credential 不设置"""
        _, kw = _classify_upstream_status(
            403, "", "", "used all the included free usage for model X blocked-user"
        )
        assert kw["quota_exhausted"] is True
        assert kw["free_quota_exhausted"] is True
        assert kw["model_quota_exhausted"] is True
        assert kw["credential_rejected"] is False

    # --- 未分类 ---

    def test_unclassified_403_all_false(self):
        """无法匹配任何模式 → 所有标记为 False"""
        _, kw = _classify_upstream_status(403, "", "", "some random error")
        assert kw["account_scoped"] is False
        assert kw["permanent_account_denial"] is False
        assert kw["quota_exhausted"] is False
        assert kw["free_quota_exhausted"] is False
        assert kw["model_quota_exhausted"] is False
        assert kw["credential_rejected"] is False


class TestClassifyUpstream429:
    """测试 429 状态码分类"""

    def test_free_usage_exhausted_429(self):
        """429 + free usage → model + free quota, account_scoped"""
        _, kw = _classify_upstream_status(
            429, "", "", "used all the included free usage for model grok-3"
        )
        assert kw["model_quota_exhausted"] is True
        assert kw["free_quota_exhausted"] is True
        assert kw["quota_exhausted"] is True
        assert kw["account_scoped"] is True

    def test_subscription_free_usage_exhausted_429(self):
        """429 + subscription:free-usage-exhausted → free quota"""
        _, kw = _classify_upstream_status(
            429, "subscription:free-usage-exhausted", "", ""
        )
        assert kw["free_quota_exhausted"] is True
        assert kw["quota_exhausted"] is True

    def test_spending_limit_429(self):
        """429 + spending-limit → quota_exhausted"""
        _, kw = _classify_upstream_status(
            429, "", "personal-team-blocked:spending-limit", ""
        )
        assert kw["quota_exhausted"] is True

    def test_plain_rate_limit_429(self):
        """429 无匹配模式 → account_scoped=True，其余 False"""
        _, kw = _classify_upstream_status(429, "", "", "just rate limited")
        assert kw["account_scoped"] is True
        assert kw["quota_exhausted"] is False
        assert kw["credential_rejected"] is False


class TestClassifyUpstreamOtherStatus:
    """测试非 401/402/403/429 的状态码"""

    @pytest.mark.parametrize("status", [200, 400, 500])
    def test_other_statuses_all_false(self, status):
        """200/400/500 → 所有标记为 False"""
        _, kw = _classify_upstream_status(status, "ERR", "type", "some message")
        for v in kw.values():
            assert v is False


class TestFingerprintFormat:
    """测试 fingerprint 格式化"""

    def test_code_normalized_lowercase(self):
        """code='ERR_AUTH' → '403:err_auth'"""
        fp, _ = _classify_upstream_status(403, "ERR_AUTH", "", "")
        assert fp.startswith("403:")
        assert "err_auth" in fp

    def test_special_chars_replaced_with_underscore(self):
        """code='invalid-token!!' → '401:invalid_token'"""
        fp, _ = _classify_upstream_status(401, "invalid-token!!", "", "")
        assert fp == "401:invalid_token"

    def test_colons_replaced_with_underscore(self):
        """code='a:b:c' → '403:a_b_c'"""
        fp, _ = _classify_upstream_status(403, "a:b:c", "", "")
        assert fp == "403:a_b_c"

    def test_empty_code_fallback_to_unknown(self):
        """code='' 且 type='' 且 message='' → '403:unknown'"""
        fp, _ = _classify_upstream_status(403, "", "", "")
        assert fp == "403:unknown"

    def test_long_code_truncated_to_48(self):
        """code 超过 48 字符 → 截断到 48"""
        long_code = "a" * 100
        fp, _ = _classify_upstream_status(403, long_code, "", "")
        code_part = fp.split(":", 1)[1]
        assert len(code_part) <= 48
