"""Unit tests for _resolve_public_url in images.py — 3-tier fallback chain.

Ported from Go 0363483 (image URL base from request).
"""

from unittest.mock import patch

from app.products.openai.images import _DEFAULT_BASE_URL, _resolve_public_url


class TestResolvePublicUrl:
    """_resolve_public_url 三层回退链测试"""

    # --- 第1优先级：显式 base_url ---

    def test_explicit_base_url_wins(self):
        url = _resolve_public_url("https://public.example", "/v1/media/images/img1")
        assert url == "https://public.example/v1/media/images/img1"

    def test_explicit_base_url_with_trailing_slash(self):
        url = _resolve_public_url("https://public.example/", "/v1/media/images/img1")
        assert url == "https://public.example/v1/media/images/img1"

    def test_explicit_base_url_different_path(self):
        url = _resolve_public_url("https://cdn.example", "/v1/files/image?id=abc")
        assert url == "https://cdn.example/v1/files/image?id=abc"

    # --- 第2优先级：配置 app_url ---

    @patch("app.products.openai.images.get_config")
    def test_config_app_url_when_base_url_none(self, mock_get_config):
        mock_cfg = mock_get_config.return_value
        mock_cfg.get_str.return_value = "https://config.example"
        url = _resolve_public_url(None, "/v1/media/images/img1")
        assert url == "https://config.example/v1/media/images/img1"
        mock_cfg.get_str.assert_called_once_with("app.app_url", "")

    @patch("app.products.openai.images.get_config")
    def test_config_app_url_rstrip_slash(self, mock_get_config):
        """配置中的尾部斜杠被去除"""
        mock_cfg = mock_get_config.return_value
        mock_cfg.get_str.return_value = "https://config.example/"
        url = _resolve_public_url(None, "/path")
        assert url == "https://config.example/path"

    # --- 第3优先级：默认回退 ---

    @patch("app.products.openai.images.get_config")
    def test_fallback_to_default_when_both_empty(self, mock_get_config):
        mock_cfg = mock_get_config.return_value
        mock_cfg.get_str.return_value = ""  # empty app_url
        url = _resolve_public_url(None, "/v1/media/images/img1")
        assert url == f"{_DEFAULT_BASE_URL}/v1/media/images/img1"

    @patch("app.products.openai.images.get_config")
    def test_fallback_with_empty_explicit_url(self, mock_get_config):
        """显式 base_url='' → 等效于 None → 走回退"""
        mock_cfg = mock_get_config.return_value
        mock_cfg.get_str.return_value = ""
        url = _resolve_public_url("", "/path")
        assert url == f"{_DEFAULT_BASE_URL}/path"

    # --- 路径拼接 ---

    def test_path_with_query_string(self):
        url = _resolve_public_url("https://example.com", "/v1/files/image?id=abc123")
        assert url == "https://example.com/v1/files/image?id=abc123"

    def test_path_with_no_leading_slash_concatenated_directly(self):
        """路径无前导 / 时按原样拼接；真实调用方总是传 /v1/... 路径"""
        url = _resolve_public_url("https://example.com", "v1/media/images/img1")
        assert url == "https://example.comv1/media/images/img1"

    def test_multiple_path_segments(self):
        url = _resolve_public_url("http://localhost:8000", "/a/b/c/d")
        assert url == "http://localhost:8000/a/b/c/d"

    # --- 边界 ---

    def test_none_base_url_empty_config(self):
        """base_url=None + config 为空 → 默认回退"""
        with patch("app.products.openai.images.get_config") as mock:
            mock.return_value.get_str.return_value = ""
            url = _resolve_public_url(None, "/x")
            assert url == f"{_DEFAULT_BASE_URL}/x"

    def test_empty_string_path(self):
        url = _resolve_public_url("https://example.com", "")
        assert url == "https://example.com"

    def test_path_is_slash_only(self):
        url = _resolve_public_url("https://example.com", "/")
        assert url == "https://example.com/"
