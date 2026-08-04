"""Input history normalization for Build API.

Port of Go cli/responses_input.go.
Preprocesses upstream input items (messages, function_calls, reasoning, etc.).
"""

from typing import Any

from app.dataplane.reverse.protocol.tool_parser import (
    normalize_function_arguments,
    schema_contains_reachable_integer,
)


def normalize_input_items(
    items: list[dict[str, Any]],
    schemas: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """normalize_input_items preprocesses input items for the Build API.

    Handles: message, function_call, function_call_output, reasoning,
    tool_search_call/output, custom_tool_call/output, apply_patch_call/output,
    agent_message, shell_call, mcp_*, compaction_trigger, additional_tools.

    Args:
        items: Input items to normalize.
        schemas: Optional map of tool name → JSON schema. When a
                 function_call's name is present and its schema requires
                 integers, its arguments JSON is normalized (12.0 → 12).
    """
    result: list[dict[str, Any]] = []

    for item in items:
        normalized = _normalize_input_item(item, schemas)
        if normalized is not None:
            result.append(normalized)

    return result


def _normalize_input_item(
    item: dict[str, Any], schemas: dict[str, Any] | None
) -> dict[str, Any] | None:
    """_normalize_input_item normalizes a single input item."""
    if not isinstance(item, dict):
        return item

    item_type = item.get("type", "")

    # Message items pass through
    if item_type == "message":
        return item

    # Function call: normalize arguments when the tool schema requires integers
    if item_type == "function_call":
        return _normalize_function_call_arguments(item, schemas)

    # Function call output: pass through
    if item_type == "function_call_output":
        return item

    # Reasoning: pass through
    if item_type == "reasoning":
        return item

    # Tool search call/output: pass through
    if item_type in ("tool_search_call", "tool_search_output"):
        return item

    # Custom tool call/output: pass through
    if item_type in ("custom_tool_call", "custom_tool_output"):
        return item

    # Apply patch call/output: pass through
    if item_type in ("apply_patch_call", "apply_patch_output"):
        return item

    # Shell/MCP call: convert to function_call
    if item_type in ("shell_call", "mcp_call"):
        return {
            "type": "function_call",
            "name": item.get("name", item_type),
            "arguments": item.get("arguments", item.get("input", {})),
        }

    # Shell/MCP output: convert to function_call_output
    if item_type in ("shell_output", "mcp_output"):
        return {
            "type": "function_call_output",
            "call_id": item.get("call_id", ""),
            "output": item.get("output", str(item.get("content", ""))),
        }

    # Agent message: convert to developer role message
    if item_type == "agent_message":
        return {
            "type": "message",
            "role": "developer",
            "content": item.get(
                "content",
                [{"type": "input_text", "text": str(item.get("message", ""))}],
            ),
        }

    # Compaction trigger: drop (handled separately)
    if item_type == "compaction_trigger":
        return None

    # Additional tools: drop (injected separately)
    if item_type == "additional_tools":
        return None

    # Unknown: pass through
    return item


def _normalize_function_call_arguments(
    item: dict[str, Any], schemas: dict[str, Any] | None
) -> dict[str, Any]:
    """_normalize_function_call_arguments rewrites integral float spellings
    in a function_call's arguments when the tool schema requires integers."""
    if not schemas:
        return item
    schema = schemas.get(item.get("name", ""))
    if not isinstance(schema, dict) or not schema_contains_reachable_integer(schema):
        return item
    arguments = item.get("arguments")
    if not isinstance(arguments, str):
        return item
    normalized, _ = normalize_function_arguments(arguments, schema)
    if normalized != arguments:
        item["arguments"] = normalized
    return item
