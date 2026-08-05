"""Custom tool emulation for Build API.

Port of Go cli/responses_custom.go.
Custom tools are wrapped as function type with input=string schema.
"""

from __future__ import annotations

import json
from typing import Any

import orjson


def normalize_custom_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """normalize_custom_tool wraps a custom tool as a function with input=string schema.

    Custom tools in the Build API need to be represented as function tools
    with a single "input" string parameter that captures the raw arguments.
    """
    name = tool.get("name", tool.get("function", {}).get("name", ""))
    description = tool.get(
        "description", tool.get("function", {}).get("description", "")
    )

    # Create function wrapper with input=string schema
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "input": {
                        "type": "string",
                        "description": f"Serialized arguments for {name}",
                    }
                },
                "required": ["input"],
            },
        },
    }


def encode_custom_tool_arguments(
    args: dict[str, Any], schema: dict[str, Any] | None = None
) -> dict[str, Any]:
    """encode_custom_tool_arguments serializes tool arguments for custom tools.

    Args are JSON-serialized and wrapped in {"input": serialized_json}.
    """
    return {"input": orjson.dumps(args).decode()}


def decode_custom_tool_input(input_body: str) -> dict[str, Any]:
    """decode_custom_tool_input parses the raw input string back to structured args."""
    try:
        return json.loads(input_body)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {"raw": input_body}
