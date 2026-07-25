"""Tests for video.py — input size validation and diagnostic sanitization."""

import pytest

from app.platform.errors import ValidationError
from app.products.openai.video import (
    _validate_video_input_size,
    _sanitize_diagnostic_text,
    _MAX_INPUT_JSON_BYTES,
    _build_segment_lengths,
    _resolve_video_size,
    validate_video_length,
)


class TestValidateVideoInputSize:
    """Test _validate_video_input_size rejects oversized payloads."""

    def test_empty_list_passes(self):
        """Empty input references — no validation error."""
        _validate_video_input_size([])

    def test_small_input_passes(self):
        """Small input references — well under limit."""
        refs = [{"image_url": f"https://example.com/img{i}.png"} for i in range(5)]
        _validate_video_input_size(refs)

    def test_no_image_url_key_passes(self):
        """Refs without image_url key — treated as empty strings, small."""
        refs = [{"file_id": "abc"}, {"file_id": "def"}]
        _validate_video_input_size(refs)

    def test_oversized_input_rejects(self):
        """Input references whose JSON exceeds 32 MiB — must raise."""
        # Create a ref with a very long URL to exceed the limit
        long_url = "https://example.com/" + "x" * (33 * 1024 * 1024)
        refs = [{"image_url": long_url}]
        with pytest.raises(ValidationError, match="exceeds"):
            _validate_video_input_size(refs)

    def test_exact_boundary_passes(self):
        """JSON payload at exactly _MAX_INPUT_JSON_BYTES should pass (not exceed)."""
        # The function uses > (strict), so exactly at the limit should pass.
        # We approximate by creating a ref whose JSON is near the limit.
        # Since orjson adds overhead (key names, brackets), we use a slightly
        # shorter URL to stay at or under the limit.
        target = _MAX_INPUT_JSON_BYTES - 50  # account for JSON overhead
        url = "https://a.com/" + "b" * max(0, target - 30)
        refs = [{"image_url": url}]
        # This should not raise — it's under or at the limit
        _validate_video_input_size(refs)


class TestSanitizeDiagnosticText:
    """Test _sanitize_diagnostic_text strips sensitive info."""

    def test_bearer_token_redacted(self):
        text = "Authorization: Bearer abc123def456"
        result = _sanitize_diagnostic_text(text)
        assert "abc123def456" not in result
        assert "REDACTED" in result

    def test_basic_auth_redacted(self):
        text = "Basic dXNlcjpwYXNz"
        result = _sanitize_diagnostic_text(text)
        assert "dXNlcjpwYXNz" not in result
        assert "REDACTED" in result

    def test_jwt_redacted(self):
        text = (
            "token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc123def456ghi789"
        )
        result = _sanitize_diagnostic_text(text)
        assert "eyJhbGciOi" not in result
        assert "JWT_REDACTED" in result

    def test_email_redacted(self):
        text = "Send to user@example.com for details"
        result = _sanitize_diagnostic_text(text)
        assert "user@example.com" not in result
        assert "EMAIL_REDACTED" in result

    def test_long_text_truncated(self):
        text = "x" * 1000
        result = _sanitize_diagnostic_text(text, max_length=512)
        assert len(result) == 515  # 512 + "..."
        assert result.endswith("...")

    def test_short_text_not_truncated(self):
        text = "short error message"
        result = _sanitize_diagnostic_text(text, max_length=512)
        assert result == text

    def test_custom_max_length(self):
        text = "a" * 100
        result = _sanitize_diagnostic_text(text, max_length=20)
        assert len(result) == 23  # 20 + "..."
        assert result.endswith("...")

    def test_clean_text_unchanged(self):
        text = "Normal error: connection timeout"
        result = _sanitize_diagnostic_text(text)
        assert result == text

    def test_multiple_tokens_redacted(self):
        text = "Bearer abc123 and Basic dXNlcjpwYXNz"
        result = _sanitize_diagnostic_text(text)
        assert "abc123" not in result
        assert "dXNlcjpwYXNz" not in result


class TestVideoHelpers:
    """Test smaller video helper functions."""

    def test_build_segment_lengths_6s(self):
        assert _build_segment_lengths(6) == [6]

    def test_build_segment_lengths_10s(self):
        assert _build_segment_lengths(10) == [10]

    def test_build_segment_lengths_12s(self):
        assert _build_segment_lengths(12) == [6, 6]

    def test_build_segment_lengths_16s(self):
        assert _build_segment_lengths(16) == [10, 6]

    def test_build_segment_lengths_20s(self):
        assert _build_segment_lengths(20) == [10, 10]

    def test_resolve_video_size_valid(self):
        ratio, res = _resolve_video_size("720x1280")
        assert ratio == "9:16"
        assert res == "720p"

    def test_resolve_video_size_invalid(self):
        with pytest.raises(ValidationError, match="size must be one of"):
            _resolve_video_size("1920x1080")

    def test_validate_video_length_valid(self):
        validate_video_length(6)  # should not raise

    def test_validate_video_length_invalid(self):
        with pytest.raises(ValidationError, match="seconds must be one of"):
            validate_video_length(8)
