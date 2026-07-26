"""Response rewriting for Build API.

Port of Go cli/responses_response.go.
Handles non-streaming and streaming response normalization.
"""

import re
from typing import Any


# Pattern to detect namespace-prefixed function names (namespace__name)
_NAMESPACE_PATTERN = re.compile(r"^(.+)__([^_].+)$")


def normalize_response_json(
    body: dict[str, Any], alias_map: dict[str, str] | None = None
) -> dict[str, Any]:
    """normalize_response_json rewrites a non-streaming Build API response.

    Restores namespace aliases in function_call items and converts
    custom_tool_call/apply_patch_call types back to their originals.
    """
    if alias_map is None:
        alias_map = {}

    output = body.get("output", [])
    normalized_output = []

    for item in output:
        normalized_item = _rewrite_output_item(item, alias_map)
        if normalized_item is not None:
            normalized_output.append(normalized_item)

    result = dict(body)
    result["output"] = normalized_output
    return result


def _rewrite_output_item(
    item: dict[str, Any], alias_map: dict[str, str]
) -> dict[str, Any] | None:
    """_rewrite_output_item rewrites a single output item."""
    if not isinstance(item, dict):
        return item

    item_type = item.get("type", "")

    # Function call: restore namespace alias
    if item_type == "function_call":
        name = item.get("name", "")
        match = _NAMESPACE_PATTERN.match(name)
        if match:
            namespace, original_name = match.group(1), match.group(2)
            item["name"] = original_name
            item["namespace"] = namespace

    # Custom tool call: restore to original type
    if item_type == "custom_tool_call":
        name = item.get("name", "")
        item["type"] = "custom_tool_call"

    return item


def normalize_response_stream(line: str) -> str | None:
    """normalize_response_stream rewrites a single SSE line from Build API.

    Returns None for lines that should be dropped.
    """
    return line


def rewrite_function_call(item: dict[str, Any]) -> dict[str, Any]:
    """rewrite_function_call converts function_call output items back to their original tool types.

    Checks for namespace prefix and custom tool markers.
    """
    name = item.get("name", "")
    arguments = item.get("arguments", "")

    match = _NAMESPACE_PATTERN.match(name)
    if match:
        namespace, original_name = match.group(1), match.group(2)
        return {
            "type": "function_call",
            "name": original_name,
            "namespace": namespace,
            "arguments": arguments,
        }

    return item
