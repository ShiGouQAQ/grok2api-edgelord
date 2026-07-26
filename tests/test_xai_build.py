"""Tests for xai_build protocol module."""

from app.dataplane.reverse.protocol.xai_build import (
    BuildStreamAdapter,
    build_build_responses_payload,
)


def test_build_payload_minimal():
    payload = build_build_responses_payload(
        model="grok-4.5", messages=[{"role": "user", "content": "hi"}]
    )
    assert payload["model"] == "grok-4.5"
    assert payload["input"][0]["content"][0]["type"] == "input_text"


def test_build_payload_effort_passthrough():
    payload = build_build_responses_payload(
        model="grok-4.5",
        messages=[{"role": "user", "content": "hi"}],
        reasoning_effort="medium",
    )
    assert payload["reasoning"]["effort"] == "medium"


def test_build_payload_effort_max_to_high():
    payload = build_build_responses_payload(
        model="grok-4.5",
        messages=[{"role": "user", "content": "hi"}],
        reasoning_effort="max",
    )
    assert payload["reasoning"]["effort"] == "high"


def test_build_payload_effort_xhigh_to_high():
    payload = build_build_responses_payload(
        model="grok-4.5",
        messages=[{"role": "user", "content": "hi"}],
        reasoning_effort="xhigh",
    )
    assert payload["reasoning"]["effort"] == "high"


def test_build_payload_stream_default_true():
    payload = build_build_responses_payload(
        model="grok-4.5", messages=[{"role": "user", "content": "hi"}]
    )
    assert payload["stream"] is True


def test_build_payload_tools_and_choice():
    payload = build_build_responses_payload(
        model="grok-4.5",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "web_search"}],
        tool_choice="auto",
    )
    assert "tools" in payload
    assert payload["tool_choice"] == "auto"


def test_build_payload_prompt_cache_key():
    payload = build_build_responses_payload(
        model="grok-4.5",
        messages=[{"role": "user", "content": "hi"}],
        prompt_cache_key="test-key",
    )
    assert payload.get("prompt_cache_key") == "test-key"


def test_build_stream_adapter_text_delta():
    adapter = BuildStreamAdapter()
    events = adapter.feed("response.output_text.delta", '{"delta":"Hello","index":0}')
    assert len(events) == 1
    assert events[0]["type"] == "text"
    assert events[0]["delta"] == "Hello"


def test_build_stream_adapter_reasoning():
    adapter = BuildStreamAdapter()
    events = adapter.feed(
        "response.output_item.done",
        '{"type":"reasoning","encrypted_content":"abc"}',
    )
    assert events[0]["type"] == "reasoning"


def test_build_stream_adapter_completed():
    adapter = BuildStreamAdapter()
    events = adapter.feed(
        "response.completed",
        '{"response":{"status":"completed","usage":{"input_tokens":10}}}',
    )
    assert events[0]["type"] == "done"
    assert events[0]["usage"]["input_tokens"] == 10


def test_build_stream_adapter_unknown_event():
    adapter = BuildStreamAdapter()
    events = adapter.feed("response.created", "{}")
    assert len(events) == 0


def test_build_stream_adapter_multiple_calls():
    adapter = BuildStreamAdapter()
    adapter.feed("response.output_text.delta", '{"delta":"Hello"}')
    events = adapter.feed("response.output_text.delta", '{"delta":" World"}')
    assert len(events) == 1
    assert events[0]["delta"] == " World"
