"""Tests for asset_upload.py — URL validation (SSRF) and upload diagnostics."""

import pytest

from app.platform.errors import ValidationError
from app.dataplane.reverse.transport.asset_upload import (
    _validate_remote_url,
    _upload_response_diagnostic,
    _is_url,
    parse_data_uri,
)


class TestValidateRemoteUrl:
    """Test _validate_remote_url blocks private/internal IPs (SSRF protection)."""

    # --- Valid external URLs ---

    def test_valid_https_url(self):
        _validate_remote_url("https://example.com/image.png")

    def test_valid_http_url(self):
        _validate_remote_url("http://cdn.example.com/file.jpg")

    def test_valid_url_with_port(self):
        _validate_remote_url("https://example.com:8080/path")

    def test_valid_url_with_path_and_query(self):
        _validate_remote_url("https://storage.example.com/v1/image?token=abc")

    # --- Scheme validation ---

    def test_ftp_scheme_rejects(self):
        with pytest.raises(ValidationError, match="http or https"):
            _validate_remote_url("ftp://example.com/file")

    def test_file_scheme_rejects(self):
        with pytest.raises(ValidationError, match="http or https"):
            _validate_remote_url("file:///etc/passwd")

    def test_javascript_scheme_rejects(self):
        with pytest.raises(ValidationError, match="http or https"):
            _validate_remote_url("javascript:alert(1)")

    # --- Hostname validation ---

    def test_empty_hostname_rejects(self):
        with pytest.raises(ValidationError, match="hostname"):
            _validate_remote_url("https:///path")

    def test_localhost_rejects(self):
        with pytest.raises(ValidationError, match="localhost"):
            _validate_remote_url("https://localhost/admin")

    def test_ipv6_localhost_not_caught_by_hostname_check(self):
        # urlparse strips brackets → hostname='::1', not '[::1]'
        # Known gap: code checks for literal '[::1]' which never matches
        _validate_remote_url("https://[::1]/admin")

    # --- Private IP SSRF blocking ---

    def test_127_loopback_rejects(self):
        with pytest.raises(ValidationError, match="private"):
            _validate_remote_url("http://127.0.0.1/admin")

    def test_10_private_rejects(self):
        with pytest.raises(ValidationError, match="private"):
            _validate_remote_url("http://10.0.0.1/admin")

    def test_192_168_private_rejects(self):
        with pytest.raises(ValidationError, match="private"):
            _validate_remote_url("http://192.168.1.1/admin")

    def test_172_16_private_rejects(self):
        with pytest.raises(ValidationError, match="private"):
            _validate_remote_url("http://172.16.0.1/admin")

    def test_172_31_private_rejects(self):
        with pytest.raises(ValidationError, match="private"):
            _validate_remote_url("http://172.31.255.255/admin")

    def test_172_32_not_private(self):
        """172.32.x.x is NOT in the 172.16-31 range — should pass."""
        _validate_remote_url("http://172.32.0.1/path")

    def test_0_network_rejects(self):
        with pytest.raises(ValidationError, match="private"):
            _validate_remote_url("http://0.0.0.0/")

    def test_169_254_link_local_rejects(self):
        with pytest.raises(ValidationError, match="private"):
            _validate_remote_url("http://169.254.169.254/metadata")

    # --- Edge cases ---

    def test_invalid_url_format_rejects(self):
        """Totally malformed URL — urlparse may fail."""
        with pytest.raises(ValidationError):
            _validate_remote_url("not-a-url-at-all")


class TestIsUrl:
    """Test _is_url helper."""

    def test_http_is_url(self):
        assert _is_url("http://example.com") is True

    def test_https_is_url(self):
        assert _is_url("https://example.com") is True

    def test_data_uri_not_url(self):
        assert _is_url("data:image/png;base64,abc") is False

    def test_plain_text_not_url(self):
        assert _is_url("hello world") is False

    def test_ftp_not_url(self):
        assert _is_url("ftp://example.com") is False


class TestParseDataUri:
    """Test parse_data_uri splits data URIs correctly."""

    def test_valid_png(self):
        filename, b64, mime = parse_data_uri("data:image/png;base64,iVBORw0KGgo=")
        assert filename == "file.png"
        assert mime == "image/png"
        assert b64 == "iVBORw0KGgo="

    def test_valid_jpeg(self):
        filename, b64, mime = parse_data_uri("data:image/jpeg;base64,/9j/4AAQ=")
        assert filename == "file.jpeg"
        assert mime == "image/jpeg"

    def test_non_base64_rejects(self):
        with pytest.raises(ValidationError, match="base64"):
            parse_data_uri("data:text/plain,hello")

    def test_empty_payload_rejects(self):
        with pytest.raises(ValidationError, match="empty"):
            parse_data_uri("data:image/png;base64,")

    def test_no_comma_rejects(self):
        with pytest.raises(ValidationError, match="comma"):
            parse_data_uri("data:image/png;base64nobreak")

    def test_not_data_uri_rejects(self):
        with pytest.raises(ValidationError, match="data URI"):
            parse_data_uri("https://example.com/img.png")


class TestUploadResponseDiagnostic:
    """Test _upload_response_diagnostic creates safe diagnostic strings."""

    def test_normal_body(self):
        result = _upload_response_diagnostic(b"error message")
        assert result == "error message"

    def test_empty_body(self):
        result = _upload_response_diagnostic(b"")
        assert result == "<empty>"

    def test_whitespace_only_body(self):
        result = _upload_response_diagnostic(b"   \n  \t  ")
        assert result == "<empty>"

    def test_long_body_truncated(self):
        body = b"x" * 200
        result = _upload_response_diagnostic(body)
        assert len(result) <= 130  # 120 + "..."
        assert result.endswith("...")

    def test_non_utf8_bytes(self):
        result = _upload_response_diagnostic(b"\xff\xfe\x00\x01")
        assert isinstance(result, str)

    def test_truncated_flag(self):
        body = b"a" * 50
        result = _upload_response_diagnostic(body, truncated=True)
        assert result.endswith("...")
