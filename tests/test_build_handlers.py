"""Smoke tests for Build provider handlers.

Tests payload construction, headers, BuildStreamAdapter, and basic flow.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.dataplane.reverse.protocol.xai_build import (
    build_build_responses_payload,
    BuildStreamAdapter,
)
from app.dataplane.proxy.adapters.headers import build_build_headers


# ---------------------------------------------------------------------------
# Payload builder tests
# ---------------------------------------------------------------------------


def test_build_payload_basic():
    payload = build_build_responses_payload(
        model="grok-4",
        messages=[{"role": "user", "content": "hello"}],
    )
    assert payload["model"] == "grok-4"
    assert payload["stream"] is True
    assert payload["store"] is False
    assert "reasoning.encrypted_content" in payload["include"]
    assert len(payload["input"]) == 1
    assert payload["input"][0]["role"] == "user"
    assert payload["input"][0]["content"][0]["text"] == "hello"


def test_build_payload_with_reasoning():
    payload = build_build_responses_payload(
        model="grok-4",
        messages=[{"role": "user", "content": "hi"}],
        reasoning_effort="high",
    )
    assert payload["reasoning"] == {"effort": "high"}


def test_build_payload_normalizes_max_effort():
    """Build API doesn't support 'max' — should normalize to 'high'."""
    payload = build_build_responses_payload(
        model="grok-4",
        messages=[{"role": "user", "content": "hi"}],
        reasoning_effort="max",
    )
    assert payload["reasoning"] == {"effort": "high"}


def test_build_payload_no_reasoning_when_none():
    payload = build_build_responses_payload(
        model="grok-4",
        messages=[{"role": "user", "content": "hi"}],
        reasoning_effort=None,
    )
    assert "reasoning" not in payload


def test_build_payload_multiple_messages():
    messages = [
        {"role": "system", "content": "You are helpful"},
        {"role": "user", "content": "hello"},
    ]
    payload = build_build_responses_payload(
        model="grok-4",
        messages=messages,
    )
    assert len(payload["input"]) == 2
    assert payload["input"][0]["role"] == "system"
    assert payload["input"][1]["role"] == "user"


def test_build_payload_with_tools():
    tools = [{"type": "function", "function": {"name": "search"}}]
    payload = build_build_responses_payload(
        model="grok-4",
        messages=[{"role": "user", "content": "hi"}],
        tools=tools,
        tool_choice="auto",
    )
    assert payload["tools"] == tools
    assert payload["tool_choice"] == "auto"


def test_build_payload_temperature_and_top_p():
    payload = build_build_responses_payload(
        model="grok-4",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.3,
        top_p=0.8,
    )
    assert payload["temperature"] == 0.3
    assert payload["top_p"] == 0.8


# ---------------------------------------------------------------------------
# BuildStreamAdapter tests
# ---------------------------------------------------------------------------


def test_adapter_text_delta():
    adapter = BuildStreamAdapter()
    import orjson

    data = orjson.dumps({"delta": "hello"}).decode()
    result = adapter.feed("response.output_text.delta", data)
    assert len(result) == 1
    assert result[0]["type"] == "text"
    assert result[0]["delta"] == "hello"
    assert adapter.full_text == "hello"


def test_adapter_done():
    adapter = BuildStreamAdapter()
    import orjson

    data = orjson.dumps(
        {"response": {"usage": {"input_tokens": 10, "output_tokens": 5}}}
    ).decode()
    result = adapter.feed("response.completed", data)
    assert len(result) == 1
    assert result[0]["type"] == "done"
    assert adapter.usage == {"input_tokens": 10, "output_tokens": 5}
    assert adapter._done is True


def test_adapter_ignores_after_done():
    adapter = BuildStreamAdapter()
    import orjson

    done_data = orjson.dumps({"response": {"usage": {}}}).decode()
    adapter.feed("response.completed", done_data)
    assert adapter._done is True

    text_data = orjson.dumps({"delta": "late"}).decode()
    result = adapter.feed("response.output_text.delta", text_data)
    assert result == []


def test_adapter_ignores_unknown_events():
    adapter = BuildStreamAdapter()
    import orjson

    data = orjson.dumps({"something": "else"}).decode()
    result = adapter.feed("response.created", data)
    assert result == []


def test_adapter_full_text_concatenation():
    adapter = BuildStreamAdapter()
    import orjson

    for word in ["hello", " ", "world"]:
        data = orjson.dumps({"delta": word}).decode()
        adapter.feed("response.output_text.delta", data)
    assert adapter.full_text == "hello world"


def test_adapter_empty_delta():
    adapter = BuildStreamAdapter()
    import orjson

    data = orjson.dumps({"delta": ""}).decode()
    result = adapter.feed("response.output_text.delta", data)
    # Empty delta still returns the dict (just doesn't append to text_buf)
    assert len(result) == 1
    assert result[0]["delta"] == ""
    assert adapter.full_text == ""


# ---------------------------------------------------------------------------
# Headers tests (additional to test_build_headers.py)
# ---------------------------------------------------------------------------


def test_build_headers_auth_format():
    headers = build_build_headers(
        access_token="my-access-token",
        agent_id="agent-123",
    )
    assert headers["Authorization"] == "Bearer my-access-token"
    assert headers["x-grok-agent-id"] == "agent-123"


def test_build_headers_model_override():
    headers = build_build_headers(
        access_token="t",
        agent_id="a",
        model="grok-4",
    )
    assert headers["x-grok-model-override"] == "grok-4"


def test_build_headers_session_id():
    headers = build_build_headers(
        access_token="t",
        agent_id="a",
        session_id="sess-abc",
    )
    assert headers["x-grok-session-id"] == "sess-abc"
    assert headers["x-grok-conv-id"] == "sess-abc"


# ---------------------------------------------------------------------------
# Stream function import test
# ---------------------------------------------------------------------------


def test_stream_build_chat_importable():
    from app.products.openai.build_chat import stream_build_chat

    assert callable(stream_build_chat)


def test_build_chat_completions_importable():
    from app.products.openai.build_chat import completions

    assert callable(completions)


def test_build_responses_create_importable():
    from app.products.openai.build_responses import create

    assert callable(create)


def test_build_messages_create_importable():
    from app.products.anthropic.build_messages import create

    assert callable(create)
