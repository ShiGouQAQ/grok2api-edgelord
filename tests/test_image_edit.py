"""Tests for xai_image_edit.py — payload building with new parameters."""

import pytest
from unittest.mock import patch, MagicMock

from app.dataplane.reverse.protocol.xai_image_edit import (
    build_image_edit_payload,
    extract_streaming_response,
    extract_model_response_urls,
    extract_model_response_file_attachments,
    IMAGE_EDIT_MODEL_NAME,
    IMAGE_EDIT_GENERATION_COUNT,
)


def _mock_config():
    """Return a mock get_config that provides sensible defaults."""
    cfg = MagicMock()
    cfg.get_bool.return_value = False
    return cfg


class TestBuildImageEditPayload:
    """Test build_image_edit_payload includes new parameters when provided."""

    @patch("app.dataplane.reverse.protocol.xai_image_edit.get_config", _mock_config)
    def test_basic_payload_no_new_params(self):
        """Backward compat: no size/aspect_ratio/streaming/partial_images."""
        payload = build_image_edit_payload(
            prompt="edit this image",
            image_references=["https://example.com/img.png"],
            parent_post_id="post_123",
        )
        assert payload["modelName"] == IMAGE_EDIT_MODEL_NAME
        assert payload["message"] == "edit this image"
        assert payload["enableImageStreaming"] is True  # default
        assert payload["imageGenerationCount"] == IMAGE_EDIT_GENERATION_COUNT

        config = payload["responseMetadata"]["modelConfigOverride"]["modelMap"][
            "imageEditModelConfig"
        ]
        assert config["imageReferences"] == ["https://example.com/img.png"]
        assert config["parentPostId"] == "post_123"
        assert "size" not in config
        assert "aspectRatio" not in config

    @patch("app.dataplane.reverse.protocol.xai_image_edit.get_config", _mock_config)
    def test_with_size(self):
        """Size parameter is included in imageEditModelConfig."""
        payload = build_image_edit_payload(
            prompt="resize",
            image_references=["https://example.com/img.png"],
            parent_post_id="post_456",
            size="1024x1024",
        )
        config = payload["responseMetadata"]["modelConfigOverride"]["modelMap"][
            "imageEditModelConfig"
        ]
        assert config["size"] == "1024x1024"

    @patch("app.dataplane.reverse.protocol.xai_image_edit.get_config", _mock_config)
    def test_with_aspect_ratio(self):
        """Aspect ratio parameter is included in imageEditModelConfig."""
        payload = build_image_edit_payload(
            prompt="crop",
            image_references=["https://example.com/img.png"],
            parent_post_id="post_789",
            aspect_ratio="16:9",
        )
        config = payload["responseMetadata"]["modelConfigOverride"]["modelMap"][
            "imageEditModelConfig"
        ]
        assert config["aspectRatio"] == "16:9"

    @patch("app.dataplane.reverse.protocol.xai_image_edit.get_config", _mock_config)
    def test_with_streaming_false(self):
        """Streaming disabled sets enableImageStreaming to False."""
        payload = build_image_edit_payload(
            prompt="no stream",
            image_references=["https://example.com/img.png"],
            parent_post_id="post_000",
            streaming=False,
        )
        assert payload["enableImageStreaming"] is False

    @patch("app.dataplane.reverse.protocol.xai_image_edit.get_config", _mock_config)
    def test_with_streaming_true(self):
        """Streaming explicitly enabled."""
        payload = build_image_edit_payload(
            prompt="stream",
            image_references=["https://example.com/img.png"],
            parent_post_id="post_001",
            streaming=True,
        )
        assert payload["enableImageStreaming"] is True

    @patch("app.dataplane.reverse.protocol.xai_image_edit.get_config", _mock_config)
    def test_with_partial_images(self):
        """Partial images overrides generation count."""
        payload = build_image_edit_payload(
            prompt="4 images",
            image_references=["https://example.com/img.png"],
            parent_post_id="post_002",
            partial_images=4,
        )
        assert payload["imageGenerationCount"] == 4

    @patch("app.dataplane.reverse.protocol.xai_image_edit.get_config", _mock_config)
    def test_partial_images_zero_uses_default(self):
        """partial_images=0 is falsy, falls back to IMAGE_EDIT_GENERATION_COUNT."""
        payload = build_image_edit_payload(
            prompt="zero",
            image_references=["https://example.com/img.png"],
            parent_post_id="post_003",
            partial_images=0,
        )
        assert payload["imageGenerationCount"] == IMAGE_EDIT_GENERATION_COUNT

    @patch("app.dataplane.reverse.protocol.xai_image_edit.get_config", _mock_config)
    def test_all_new_params(self):
        """All new parameters provided simultaneously."""
        payload = build_image_edit_payload(
            prompt="everything",
            image_references=["https://example.com/a.png", "https://example.com/b.png"],
            parent_post_id="post_full",
            size="1792x1024",
            aspect_ratio="16:9",
            streaming=False,
            partial_images=6,
        )
        config = payload["responseMetadata"]["modelConfigOverride"]["modelMap"][
            "imageEditModelConfig"
        ]
        assert config["size"] == "1792x1024"
        assert config["aspectRatio"] == "16:9"
        assert payload["enableImageStreaming"] is False
        assert payload["imageGenerationCount"] == 6
        assert len(config["imageReferences"]) == 2

    @patch("app.dataplane.reverse.protocol.xai_image_edit.get_config", _mock_config)
    def test_empty_size_not_included(self):
        """Empty string size is falsy — not included."""
        payload = build_image_edit_payload(
            prompt="test",
            image_references=[],
            parent_post_id="post",
            size="",
        )
        config = payload["responseMetadata"]["modelConfigOverride"]["modelMap"][
            "imageEditModelConfig"
        ]
        assert "size" not in config

    @patch("app.dataplane.reverse.protocol.xai_image_edit.get_config", _mock_config)
    def test_empty_aspect_ratio_not_included(self):
        """Empty string aspect_ratio is falsy — not included."""
        payload = build_image_edit_payload(
            prompt="test",
            image_references=[],
            parent_post_id="post",
            aspect_ratio="",
        )
        config = payload["responseMetadata"]["modelConfigOverride"]["modelMap"][
            "imageEditModelConfig"
        ]
        assert "aspectRatio" not in config


class TestExtractStreamingResponse:
    """Test extract_streaming_response parses nested JSON."""

    def test_valid_streaming_response(self):
        data = {
            "result": {
                "response": {
                    "streamingImageGenerationResponse": {"status": "generating"}
                }
            }
        }
        result = extract_streaming_response(data)
        assert result == {"status": "generating"}

    def test_no_result(self):
        assert extract_streaming_response({}) is None

    def test_no_response(self):
        assert extract_streaming_response({"result": {}}) is None

    def test_no_streaming_key(self):
        assert extract_streaming_response({"result": {"response": {}}}) is None


class TestExtractModelResponseUrls:
    """Test extract_model_response_urls parses nested JSON."""

    def test_valid_urls(self):
        data = {
            "result": {
                "response": {"modelResponse": {"generatedImageUrls": ["url1", "url2"]}}
            }
        }
        assert extract_model_response_urls(data) == ["url1", "url2"]

    def test_empty_urls(self):
        data = {"result": {"response": {"modelResponse": {"generatedImageUrls": []}}}}
        assert extract_model_response_urls(data) == []

    def test_no_model_response(self):
        assert extract_model_response_urls({"result": {"response": {}}}) == []


class TestExtractModelResponseFileAttachments:
    """Test extract_model_response_file_attachments parses nested JSON."""

    def test_valid_attachments(self):
        data = {
            "result": {
                "response": {"modelResponse": {"fileAttachments": ["id1", "id2"]}}
            }
        }
        assert extract_model_response_file_attachments(data) == ["id1", "id2"]

    def test_empty_attachments(self):
        data = {"result": {"response": {"modelResponse": {"fileAttachments": []}}}}
        assert extract_model_response_file_attachments(data) == []

    def test_filters_non_strings(self):
        data = {
            "result": {
                "response": {
                    "modelResponse": {"fileAttachments": ["id1", 123, None, "id2"]}
                }
            }
        }
        assert extract_model_response_file_attachments(data) == ["id1", "id2"]
