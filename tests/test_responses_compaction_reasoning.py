import pytest
from app.dataplane.reverse.protocol.responses_compaction import (
    encode_compaction,
    decode_compaction,
    prepare_gateway_compaction_sample,
    clean_gateway_compaction_summary,
    build_gateway_compaction_response,
)
from app.dataplane.reverse.protocol.responses_reasoning_recovery import (
    recover_reasoning_decode_failure,
    strip_reasoning_encrypted_content,
)

# --- Compaction ---


def test_encode_decode_roundtrip():
    items = [{"type": "message", "role": "user", "content": "hello"}]
    encoded = encode_compaction(items, "test-key")
    decoded = decode_compaction(encoded, "test-key")
    assert decoded is not None
    assert len(decoded) == 1
    assert decoded[0]["content"] == "hello"


def test_decode_invalid_prefix():
    assert decode_compaction("invalid-prefix-data", "key") is None


def test_decode_corrupted():
    assert decode_compaction("g2a_compact_v1.not-base64!!!", "key") is None


def test_compaction_has_prefix():
    items = [{"type": "message", "role": "user", "content": "hi"}]
    encoded = encode_compaction(items, "key")
    assert encoded.startswith("g2a_compact_v1.")


def test_different_keys_produce_different_output():
    items = [{"type": "message", "content": "hi"}]
    e1 = encode_compaction(items, "key1")
    e2 = encode_compaction(items, "key2")
    assert e1 != e2


def test_prepare_sample_empty():
    sample = prepare_gateway_compaction_sample([])
    assert sample["truncated"] is False
    assert "history" not in sample


def test_prepare_sample_small():
    items = [{"type": "message", "content": f"msg {i}"} for i in range(5)]
    sample = prepare_gateway_compaction_sample(items)
    assert sample["truncated"] is False
    assert len(sample["history"]) == 5


def test_prepare_sample_truncated():
    items = [{"type": "message", "content": f"msg {i}"} for i in range(30)]
    sample = prepare_gateway_compaction_sample(items)
    assert sample["truncated"] is True


def test_clean_summary_strips_analysis():
    cleaned = clean_gateway_compaction_summary(
        "<analysis>some analysis</analysis>remaining"
    )
    assert "analysis" not in cleaned
    assert cleaned.strip() == "remaining"


def test_clean_summary_strips_summary():
    cleaned = clean_gateway_compaction_summary("<summary>summary text</summary>rest")
    assert cleaned.strip() == "rest"


def test_build_compaction_response():
    body = {
        "output": [
            {"type": "compaction_trigger", "compacted_input": "compressed summary"}
        ]
    }
    result = build_gateway_compaction_response(body, "responses")
    assert result is not None
    assert "compressed summary" in result["input"][0]["content"]


def test_build_compaction_response_no_match():
    body = {"output": [{"type": "message", "content": [{"text": "hello"}]}]}
    assert build_gateway_compaction_response(body, "responses") is None


# --- Reasoning Recovery ---


def test_recover_strips_encrypted():
    body = {"output": [{"type": "reasoning", "encrypted_content": "abc123"}]}
    result, warning = recover_reasoning_decode_failure(body, None)
    assert "encrypted_content" not in result["output"][0]
    assert warning is not None


def test_recover_no_reasoning():
    body = {"output": [{"type": "message", "content": []}]}
    result, warning = recover_reasoning_decode_failure(body, "key")
    assert warning is None
    assert result is body  # unchanged


def test_recover_clears_cache():
    body = {
        "output": [{"type": "reasoning", "encrypted_content": "abc"}],
        "prompt_cache_key": "cached-key",
    }
    result, warning = recover_reasoning_decode_failure(body, "cached-key")
    assert "prompt_cache_key" not in result
    assert "cache-cleared" in (warning or "")


def test_strip_reasoning_encrypted():
    body = {"output": [{"type": "reasoning", "encrypted_content": "secret"}]}
    result = strip_reasoning_encrypted_content(body)
    assert "encrypted_content" not in result["output"][0]
