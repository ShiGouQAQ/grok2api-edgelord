"""Tests for media audit logging — port of chenyme/grok2api c936ab1.

Ports backend/internal/application/gateway/response_media_audit_test.go plus
the tool-output normalization contracts from responses_codex_tools.go /
responses_input.go.
"""

import json
from types import SimpleNamespace

import orjson
import pytest

from app.platform.storage.media_audit import (
    is_function_call_output_content_array,
    log_media_input_summary,
    log_response_media_summary,
    normalize_function_call_output_input,
    normalize_input_image_part,
    summarize_response_media,
)


async def _async_noop(*args, **kwargs):
    return None


class _FakeLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple]] = []

    def debug(self, message: str, *args: object, **kwargs: object) -> None:
        self.calls.append(("debug", message, args))

    def warning(self, message: str, *args: object, **kwargs: object) -> None:
        self.calls.append(("warning", message, args))


class TestSummarizeResponseMedia:
    def test_scans_messages_and_function_outputs(self):
        """input array with message + function_call_output image blocks."""
        body = (
            b'{"input":['
            b'{"type":"message","role":"user","content":['
            b'{"type":"input_text","text":"hello"},'
            b'{"type":"input_image","image_url":"data:image/png;base64,aGVsbG8="}'
            b"]},"
            b'{"type":"function_call_output","call_id":"call_1","output":['
            b'{"type":"input_text","text":"tool"},'
            b'{"type":"input_image","image_url":"data:image/jpeg;base64,QUJD"},'
            b'{"type":"input_image","file_id":"file_1"}'
            b"]}]}"
        )
        summary = summarize_response_media(body)
        assert summary == {
            "input_images": 3,
            "image_bytes": 8,
            "content_arrays": 2,
            "text_bytes": 9,
        }

    def test_supports_chat_and_anthropic_blocks(self):
        """messages array with chat image_url and anthropic tool_result/image."""
        body = (
            b'{"messages":['
            b'{"role":"user","content":['
            b'{"type":"text","text":"chat"},'
            b'{"type":"image_url","image_url":{"url":"data:image/webp;base64,QUJDRA=="}}'
            b"]},"
            b'{"role":"user","content":['
            b'{"type":"tool_result","tool_use_id":"tool_1","content":['
            b'{"type":"text","text":"anthropic"},'
            b'{"type":"image","source":{"type":"base64","media_type":"image/png","data":"aGVsbG8="}}'
            b"]}]}"
            b"]}"
        )
        summary = summarize_response_media(body)
        assert summary == {
            "input_images": 2,
            "image_bytes": 9,
            "content_arrays": 3,
            "text_bytes": 13,
        }

    def test_does_not_interpret_arbitrary_image_content(self):
        # Lowercase "image" passes the pre-filter; the structured parse must
        # not count an object-typed output as image content.
        body = (
            b'{"input":[{"type":"function_call_output","call_id":"call_1",'
            b'"output":{"image_content":{"data":"aGVsbG8="}}}],'
            b'"messages":[]}'
        )
        summary = summarize_response_media(body)
        assert summary is not None
        assert summary["input_images"] == 0
        assert summary["image_bytes"] == 0
        assert summary["content_arrays"] == 0
        assert summary["text_bytes"] == 0

    def test_capitalized_image_token_skips_decode(self, monkeypatch):
        """ImageContent (capital I) misses the lowercase guard -> None."""
        decode_calls: list = []

        def _spy_loads(*args, **kwargs):  # pragma: no cover - must not be called
            decode_calls.append(args)
            return json.loads(*args, **kwargs)

        monkeypatch.setattr("app.platform.storage.media_audit.json.loads", _spy_loads)
        body = (
            b'{"input":[{"type":"function_call_output","call_id":"call_1",'
            b'"output":{"ImageContent":{"data":"aGVsbG8="}}}]}'
        )
        assert summarize_response_media(body) is None
        assert decode_calls == []

    def test_pure_text_content_arrays_count_arrays_but_no_images(self):
        body = (
            b'{"input":['
            b'{"type":"message","role":"user","content":'
            b'[{"type":"input_text","text":"describe this image"}]},'
            b'{"type":"function_call_output","call_id":"call_1","output":'
            b'[{"type":"input_text","text":"done"}]}'
            b"]}"
        )
        summary = summarize_response_media(body)
        assert summary is not None
        assert summary["input_images"] == 0
        assert summary["content_arrays"] == 2

    def test_no_image_keyword_returns_none_without_json_decode(self, monkeypatch):
        """Hot-path guard: no JSON decode when body lacks the 'image' token."""
        decode_calls: list = []

        def _spy_loads(*args, **kwargs):  # pragma: no cover - must not be called
            decode_calls.append(args)
            return json.loads(*args, **kwargs)

        monkeypatch.setattr("app.platform.storage.media_audit.json.loads", _spy_loads)
        body = b'{"input":[{"type":"input_text","text":"hello"}]}'
        assert summarize_response_media(body) is None
        assert decode_calls == []

    def test_invalid_json_with_image_keyword_returns_none(self):
        assert summarize_response_media(b'{"messages": [image') is None

    def test_non_object_root_returns_zero_summary(self):
        summary = summarize_response_media(b'["image", "list"]')
        assert summary is not None
        assert summary["input_images"] == 0


class TestLogResponseMediaSummary:
    def test_logs_only_metadata_at_debug_when_input_images(self):
        logger = _FakeLogger()
        log_response_media_summary(
            logger,
            "req-media",
            {
                "input_images": 42,
                "image_bytes": 33_030_144,
                "content_arrays": 42,
                "text_bytes": 840,
            },
        )
        assert len(logger.calls) == 1
        level, message, args = logger.calls[0]
        assert level == "debug"
        assert message == (
            "request_media_input_summary request_id={} media_input_images={} "
            "media_input_image_bytes={} media_content_arrays={} media_text_bytes={}"
        )
        assert args == ("req-media", 42, 33_030_144, 42, 840)
        # No payload values (base64, tokens, account ids) in the log.
        logged = message.format(*args)
        for forbidden in ("base64", "Authorization", "Cookie", "account", "aGVsbG8"):
            assert forbidden not in logged

    def test_skips_log_when_no_input_images(self):
        logger = _FakeLogger()
        log_response_media_summary(
            logger,
            "req-text",
            {
                "input_images": 0,
                "image_bytes": 0,
                "content_arrays": 2,
                "text_bytes": 22,
            },
        )
        assert logger.calls == []

    def test_skips_log_for_none_summary(self):
        logger = _FakeLogger()
        log_response_media_summary(logger, "req-text", None)
        assert logger.calls == []


class TestDecodedBase64Bytes:
    def test_table(self):
        from app.platform.storage.media_audit import decoded_base64_bytes

        for encoded, want in {
            "aGVsbG8=": 5,
            "QUJD": 3,
            "QUI": 2,
            "QQ": 1,
            "bad!": 0,
            "QQ=A": 0,
        }.items():
            assert decoded_base64_bytes(encoded) == want


class TestIsFunctionCallOutputContentArray:
    def test_input_prefixed_blocks_are_content_array(self):
        blocks = [
            {"type": "input_text", "text": "a"},
            {"type": "input_image", "image_url": "data:image/png;base64,x"},
        ]
        assert is_function_call_output_content_array(blocks) is True

    def test_plain_structured_array_is_not_content_array(self):
        blocks = [{"type": "text", "text": "a"}, {"name": "tool"}]
        assert is_function_call_output_content_array(blocks) is False

    def test_empty_array_is_not_content_array(self):
        assert is_function_call_output_content_array([]) is False

    def test_non_dict_entries_are_skipped(self):
        blocks = ["raw", {"type": "input_image", "image_url": "u"}]
        assert is_function_call_output_content_array(blocks) is True
        assert is_function_call_output_content_array(["raw", {"type": "text"}]) is False


class TestNormalizeInputImagePart:
    def test_auto_low_high_pass_through(self):
        for detail in ("auto", "low", "high"):
            converted = normalize_input_image_part(
                {"image_url": "data:image/png;base64,x", "detail": detail}, "in[0]"
            )
            assert converted == {
                "type": "input_image",
                "detail": detail,
                "image_url": "data:image/png;base64,x",
            }

    def test_missing_or_empty_detail_defaults_to_auto(self):
        assert normalize_input_image_part({"image_url": "u"}, "in[0]") == {
            "type": "input_image",
            "detail": "auto",
            "image_url": "u",
        }
        assert (
            normalize_input_image_part({"image_url": "u", "detail": "  "}, "in[0]")[
                "detail"
            ]
            == "auto"
        )

    def test_original_downgraded_to_high_with_warning(self, monkeypatch):
        warnings: list[str] = []
        monkeypatch.setattr(
            "app.platform.storage.media_audit.logger.warning",
            lambda message, *args: warnings.append(message),
        )
        converted = normalize_input_image_part(
            {"image_url": "u", "detail": "original"}, "in[0]"
        )
        assert converted["detail"] == "high"
        assert warnings, "expected a warning when downgrading original -> high"
        assert "original" in warnings[0]

    def test_rejects_unknown_detail(self):
        with pytest.raises(ValueError, match="detail"):
            normalize_input_image_part({"image_url": "u", "detail": "bogus"}, "in[0]")

    def test_rejects_non_string_detail(self):
        with pytest.raises(ValueError, match="detail"):
            normalize_input_image_part({"image_url": "u", "detail": 5}, "in[0]")

    def test_url_alias_maps_to_image_url(self):
        converted = normalize_input_image_part({"url": "https://x/img.png"}, "in[0]")
        assert converted["image_url"] == "https://x/img.png"

    def test_file_id_preserved(self):
        converted = normalize_input_image_part(
            {"file_id": "file_1", "detail": "high"}, "in[0]"
        )
        assert converted["file_id"] == "file_1"


class TestNormalizeFunctionCallOutputInput:
    def test_content_array_output_preserves_input_image_blocks(self):
        converted = normalize_function_call_output_input(
            {
                "call_id": "call_1",
                "output": [
                    {"type": "input_text", "text": "tool"},
                    {"type": "input_image", "image_url": "data:image/png;base64,x"},
                ],
            },
            "input[0]",
        )
        assert converted["type"] == "function_call_output"
        assert converted["call_id"] == "call_1"
        assert converted["output"] == [
            {"type": "input_text", "text": "tool"},
            {
                "type": "input_image",
                "detail": "auto",
                "image_url": "data:image/png;base64,x",
            },
        ]

    def test_mixed_array_with_input_block_treated_as_content_array(self):
        with pytest.raises(ValueError):
            normalize_function_call_output_input(
                {
                    "call_id": "call_1",
                    "output": [
                        {"type": "input_text", "text": "a"},
                        {"type": "unrelated", "k": 1},
                    ],
                },
                "input[0]",
            )

    def test_plain_output_falls_back_to_json_string(self):
        converted = normalize_function_call_output_input(
            {"call_id": "call_1", "output": {"ImageContent": {"data": "aGVsbG8="}}},
            "input[0]",
        )
        assert converted["output"] == '{"ImageContent": {"data": "aGVsbG8="}}'

    def test_string_output_passes_through(self):
        converted = normalize_function_call_output_input(
            {"call_id": "call_1", "output": "done"}, "input[0]"
        )
        assert converted["output"] == "done"

    def test_none_output_becomes_empty_string(self):
        converted = normalize_function_call_output_input(
            {"call_id": "call_1", "output": None}, "input[0]"
        )
        assert converted["output"] == ""

    def test_missing_call_id_rejected(self):
        with pytest.raises(ValueError, match="call_id"):
            normalize_function_call_output_input({"output": "x"}, "input[0]")

    def test_whitespace_call_id_rejected(self):
        with pytest.raises(ValueError, match="call_id"):
            normalize_function_call_output_input(
                {"call_id": "  ", "output": "x"}, "input[0]"
            )


class TestLogMediaInputSummaryRealBody:
    """G2-1: the audit is fed the REAL request body, so image-bearing requests
    emit the DEBUG record (Go c936ab1 gateway/service.go audits input.Body)."""

    def test_real_messages_body_with_images_emits_debug_record(self):
        logger = _FakeLogger()
        body = orjson.dumps(
            {
                "model": "grok-4.20",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "describe"},
                            {
                                "type": "image_url",
                                "image_url": {"url": "data:image/png;base64,aGVsbG8="},
                            },
                        ],
                    }
                ],
            }
        )
        log_media_input_summary(logger, "req-real", body)
        assert len(logger.calls) == 1
        level, message, args = logger.calls[0]
        assert level == "debug"
        assert "request_media_input_summary" in message
        assert args[1] == 1  # media_input_images

    def test_real_text_body_without_images_stays_silent(self):
        logger = _FakeLogger()
        body = orjson.dumps(
            {"model": "grok-4.20", "messages": [{"role": "user", "content": "hi"}]}
        )
        log_media_input_summary(logger, "req-text", body)
        assert logger.calls == []


class TestRealBodyAuditWiring:
    """G2-1: image/video entry points audit the actual request payload, not a
    synthetic {"model","prompt","n"} / {"input_references"} subset."""

    def test_generate_audits_full_request_body(self, monkeypatch):
        import asyncio

        from app.products.openai import images as images_mod

        spec = SimpleNamespace(
            mode_id=2,
            pool_candidates=lambda: ["super"],
            model_name="grok-imagine-image",
        )
        monkeypatch.setattr(images_mod, "resolve_model", lambda model: spec)
        monkeypatch.setattr(images_mod, "selection_max_retries", lambda: 1)

        class _Dir:
            async def reserve_any(self, *args, **kwargs):
                raise RuntimeError("downstream not reached in this test")

        monkeypatch.setattr("app.dataplane.account._directory", _Dir())

        bodies: list[bytes] = []
        monkeypatch.setattr(
            images_mod,
            "log_media_input_summary",
            lambda logger_obj, request_id, body: bodies.append(body),
        )

        with pytest.raises(RuntimeError):
            asyncio.run(
                images_mod.generate(
                    model="grok-imagine-image",
                    prompt="draw a cat",
                    n=2,
                    size="1024x1024",
                    response_format="b64_json",
                    stream=False,
                )
            )

        assert len(bodies) == 1
        payload = json.loads(bodies[0])
        assert payload == {
            "model": "grok-imagine-image",
            "prompt": "draw a cat",
            "n": 2,
            "size": "1024x1024",
            "response_format": "b64_json",
            "stream": False,
        }

    def test_create_video_audits_full_request_body(self, monkeypatch):
        import asyncio

        from app.products.openai import video as video_mod

        spec = SimpleNamespace(enabled=True, is_video=lambda: True)
        monkeypatch.setattr(
            video_mod, "model_registry", SimpleNamespace(get=lambda _: spec)
        )
        monkeypatch.setattr(video_mod, "_put_video_job", _async_noop)
        monkeypatch.setattr(video_mod, "_run_video_job", _async_noop)
        monkeypatch.setattr(video_mod, "_expire_video_job", _async_noop)

        bodies: list[bytes] = []
        monkeypatch.setattr(
            video_mod,
            "log_media_input_summary",
            lambda logger_obj, request_id, body: bodies.append(body),
        )

        asyncio.run(
            video_mod.create_video(
                model="grok-imagine-video",
                prompt="a cat walking",
                seconds=6,
                size="720x1280",
                input_references=[{"image_url": "data:image/png;base64,aGVsbG8="}],
            )
        )

        assert len(bodies) == 1
        payload = json.loads(bodies[0])
        assert payload["model"] == "grok-imagine-video"
        assert payload["prompt"] == "a cat walking"
        assert payload["seconds"] == "6"
        assert payload["size"] == "720x1280"
        assert payload["input_references"] == [
            {"image_url": "data:image/png;base64,aGVsbG8="}
        ]
