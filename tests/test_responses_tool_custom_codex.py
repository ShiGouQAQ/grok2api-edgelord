import json

from app.dataplane.reverse.protocol.responses_tool_custom import (
    decode_custom_tool_input,
    encode_custom_tool_arguments,
    normalize_custom_tool,
)
from app.dataplane.reverse.protocol.responses_tool_codex import (
    normalize_apply_patch_tool,
    normalize_legacy_local_shell_tool,
    normalize_shell_tool,
)

# --- Custom Tool ---


def test_custom_tool_wraps_as_function():
    result = normalize_custom_tool({"name": "my-tool", "description": "A custom tool"})
    assert result["type"] == "function"
    assert result["function"]["name"] == "my-tool"
    assert "input" in result["function"]["parameters"]["properties"]


def test_custom_tool_requires_input():
    result = normalize_custom_tool({"name": "test"})
    assert "input" in result["function"]["parameters"]["required"]


def test_encode_arguments():
    encoded = encode_custom_tool_arguments({"key": "value"})
    parsed = json.loads(encoded["input"])
    assert parsed["key"] == "value"


def test_decode_input():
    decoded = decode_custom_tool_input('{"key": "value"}')
    assert decoded["key"] == "value"


def test_decode_invalid_input():
    decoded = decode_custom_tool_input("not-json")
    assert "raw" in decoded


# --- Codex Tool Bridges ---


def test_shell_tool_has_command():
    result = normalize_shell_tool({})
    assert result["type"] == "function"
    assert "command" in result["function"]["parameters"]["properties"]


def test_shell_tool_preserves_name():
    result = normalize_shell_tool({"name": "bash", "function": {"name": "bash"}})
    assert result["function"]["name"] == "bash"


def test_apply_patch_tool_has_patch():
    result = normalize_apply_patch_tool({})
    assert "patch" in result["function"]["parameters"]["properties"]


def test_apply_patch_preserves_name():
    result = normalize_apply_patch_tool({"name": "apply_patch"})
    assert result["function"]["name"] == "apply_patch"


def test_legacy_local_shell():
    result = normalize_legacy_local_shell_tool({"function": {"name": "local_shell"}})
    assert result["type"] == "function"
    assert result["function"]["name"] == "local_shell"
    assert "command" in result["function"]["parameters"]["properties"]


def test_shell_tool_custom_params_preserved():
    result = normalize_shell_tool(
        {
            "function": {
                "name": "custom_shell",
                "description": "Run a command",
                "parameters": {
                    "type": "object",
                    "properties": {"cmd": {"type": "string"}},
                    "required": ["cmd"],
                },
            }
        }
    )
    assert result["function"]["description"] == "Run a command"
