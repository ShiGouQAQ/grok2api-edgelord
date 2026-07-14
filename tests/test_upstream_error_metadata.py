"""上游错误元数据提取单元测试

测试 _extract_error_metadata() 和 _normalize_failure_code() 函数：
- 从 JSON 错误 body 中提取 code/type/message
- 规范化错误码用于指纹去重
- 覆盖空值、非 JSON、嵌套结构、截断等边界情况
"""

import pytest
from app.platform.errors import _extract_error_metadata, _normalize_failure_code


class TestExtractErrorMetadata:
    """测试 _extract_error_metadata 函数"""

    def test_empty_body(self):
        """空 body 返回空三元组"""
        assert _extract_error_metadata("") == ("", "", "")

    def test_nested_error_dict(self):
        """嵌套 error 字典正常提取 code/type/message"""
        body = '{"error":{"code":"AUTH_ERR","type":"auth","message":"bad token"}}'
        assert _extract_error_metadata(body) == ("AUTH_ERR", "auth", "bad token")

    def test_minimal_error_dict(self):
        """error 字典只有 code，type 和 message 缺失时返回空字符串"""
        body = '{"error":{"code":"X"}}'
        assert _extract_error_metadata(body) == ("X", "", "")

    def test_error_error_fallback(self):
        """error.message 缺失时回退到 error.error 作为 message"""
        body = '{"error":{"error":"fallback msg"}}'
        assert _extract_error_metadata(body) == ("", "", "fallback msg")

    def test_top_level_keys_no_nested_error(self):
        """无嵌套 error 时从顶层 key 提取"""
        body = '{"code":"TOP_LEVEL","error":"top level error","message":"top msg"}'
        assert _extract_error_metadata(body) == ("TOP_LEVEL", "", "top level error")

    def test_string_error_value(self):
        """error 为字符串时正确提取 message"""
        body = '{"error":"string error"}'
        assert _extract_error_metadata(body) == ("", "", "string error")

    def test_non_json_body(self):
        """非 JSON 字符串截断到 200 字符返回"""
        assert _extract_error_metadata("not json") == ("", "", "not json")

    def test_json_array(self):
        """JSON 数组（非 dict）返回空三元组"""
        assert _extract_error_metadata('["array"]') == ("", "", "")

    def test_json_null(self):
        """JSON null 返回空三元组"""
        assert _extract_error_metadata("null") == ("", "", "")

    def test_truncation_at_200_chars(self):
        """超过 200 字符的非 JSON 字符串截断为 200 字符"""
        long = "x" * 250
        result = _extract_error_metadata(long)
        assert result == ("", "", "x" * 200)


class TestNormalizeFailureCode:
    """测试 _normalize_failure_code 函数"""

    def test_normal_code(self):
        """正常错误码转小写"""
        assert _normalize_failure_code("ERR_AUTH") == "err_auth"

    def test_special_chars(self):
        """特殊字符替换为下划线并清理"""
        assert _normalize_failure_code("invalid-token!!") == "invalid_token"

    def test_colons(self):
        """冒号替换为下划线"""
        assert _normalize_failure_code("a:b:c") == "a_b_c"

    def test_leading_trailing_underscores(self):
        """首尾下划线被去除"""
        assert _normalize_failure_code("___test___") == "test"

    def test_truncation(self):
        """超过 48 字符截断"""
        assert _normalize_failure_code("A" * 100) == "a" * 48

    def test_empty_string(self):
        """空字符串返回空"""
        assert _normalize_failure_code("") == ""

    def test_all_separators(self):
        """全为分隔符时返回空"""
        assert _normalize_failure_code("---") == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
