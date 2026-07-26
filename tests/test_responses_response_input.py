import pytest
from app.dataplane.reverse.protocol.responses_response import (
    normalize_response_json,
    rewrite_function_call,
)
from app.dataplane.reverse.protocol.responses_input import (
    normalize_input_items,
)

# --- Response Rewriting ---


def test_response_json_passthrough():
    body = {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Hello"}],
            }
        ]
    }
    result = normalize_response_json(body)
    assert len(result["output"]) == 1


def test_function_call_namespace_restored():
    body = {
        "output": [{"type": "function_call", "name": "ns__func", "arguments": "{}"}]
    }
    result = normalize_response_json(body)
    item = result["output"][0]
    assert item["name"] == "func"
    assert item.get("namespace") == "ns"


def test_function_call_no_namespace():
    body = {
        "output": [{"type": "function_call", "name": "simple_func", "arguments": "{}"}]
    }
    result = normalize_response_json(body)
    assert result["output"][0]["name"] == "simple_func"


def test_rewrite_function_call_with_namespace():
    result = rewrite_function_call({"name": "ns__func", "arguments": "{}"})
    assert result["name"] == "func"
    assert result["namespace"] == "ns"


def test_rewrite_function_call_no_namespace():
    result = rewrite_function_call({"name": "func", "arguments": "{}"})
    assert result["name"] == "func"


def test_response_json_empty_output():
    assert normalize_response_json({"output": []})["output"] == []


# --- Input Normalization ---


def test_input_message_passthrough():
    items = [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "hi"}],
        }
    ]
    result = normalize_input_items(items)
    assert len(result) == 1
    assert result[0]["type"] == "message"


def test_input_function_call():
    items = [{"type": "function_call", "name": "test", "arguments": "{}"}]
    result = normalize_input_items(items)
    assert result[0]["type"] == "function_call"


def test_input_reasoning():
    items = [{"type": "reasoning", "encrypted_content": "abc"}]
    result = normalize_input_items(items)
    assert result[0]["type"] == "reasoning"


def test_input_compact_trigger_dropped():
    items = [{"type": "compaction_trigger", "content": "...very long..."}]
    result = normalize_input_items(items)
    assert len(result) == 0


def test_input_additional_tools_dropped():
    items = [{"type": "additional_tools", "tools": []}]
    result = normalize_input_items(items)
    assert len(result) == 0


def test_input_agent_message_to_developer():
    items = [{"type": "agent_message", "message": "I'll help"}]
    result = normalize_input_items(items)
    assert result[0]["type"] == "message"
    assert result[0]["role"] == "developer"


def test_input_shell_call_converted():
    items = [{"type": "shell_call", "name": "bash", "arguments": {"cmd": "ls"}}]
    result = normalize_input_items(items)
    assert result[0]["type"] == "function_call"


def test_input_empty():
    assert normalize_input_items([]) == []


def test_input_unknown_passthrough():
    items = [{"type": "unknown_new_type", "data": "test"}]
    result = normalize_input_items(items)
    assert len(result) == 1
