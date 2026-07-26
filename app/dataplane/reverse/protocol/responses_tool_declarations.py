"""Tool declaration normalization for Build API.

Port of Go cli/responses_tool_declarations.go.

Normalizes OpenAI-format tool declarations into the flat function format
the Build API expects. Handles:
  - Hosted tools (web_search, x_search, image_generation) → passthrough
  - Function tools → passthrough with parameter root normalization
  - Namespace tools → flatten children with ``{namespace}__{name}`` prefix
  - Custom / MCP / Shell / local_shell / apply_patch → normalize to function
  - Unknown types → raise ToolNormalizationError
"""

from __future__ import annotations

import copy
from typing import Any

# ---------------------------------------------------------------------------
# Tool type sets
# ---------------------------------------------------------------------------

# Tools the Build API handles natively — pass through unchanged.
_HOSTED_TOOL_TYPES: frozenset[str] = frozenset(
    {"web_search", "x_search", "image_generation"}
)

# Tool types that get normalized to "function" type.
_NORMALIZED_TOOL_TYPES: frozenset[str] = frozenset(
    {"custom", "mcp", "shell", "local_shell", "apply_patch"}
)

# Web search variant names accepted by the Build API.
_WEB_SEARCH_VARIANTS: frozenset[str] = frozenset(
    {
        "web_search",
        "web_search_preview",
        "web_search_preview_2025_03_11",
        "web_search_2025_08_26",
    }
)

# Additional native tool types normalized to function.
_NATIVE_TOOL_TYPES: frozenset[str] = frozenset(
    {"collections_search", "file_search", "code_execution", "code_interpreter"}
)

# Explicitly rejected tool types.
_UNSUPPORTED_TOOL_TYPES: frozenset[str] = frozenset({"computer_use_preview"})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class ToolNormalizationError(Exception):
    """Raised when a tool declaration is invalid and cannot be normalized."""

    def __init__(
        self, message: str, param: str | None = None, code: str = "invalid_parameter"
    ) -> None:
        self.param = param
        self.code = code
        super().__init__(message)


def normalize_responses_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenAI tool declarations to Build API format.

    Args:
        tools: List of tool declarations from the client (OpenAI format).

    Returns:
        Normalized tool list ready for the Build API.

    Raises:
        ToolNormalizationError: If a tool declaration is invalid.
    """
    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    for index, tool in enumerate(tools):
        param = f"tools[{index}]"
        items = _normalize_tool(tool, param=param)
        if items is None:
            continue
        for normalized in items:
            name = _tool_name(normalized)
            if name and name in seen:
                continue
            if name:
                seen.add(name)
            result.append(normalized)

    return result


def build_hosted_tool_declarations() -> list[dict[str, Any]]:
    """Return the standard hosted tool set for Build API routes.

    These are injected automatically for cache-capable routes.
    """
    return [
        {"type": "web_search", "enable_image_understanding": True},
        {"type": "x_search", "enable_video_understanding": True},
    ]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_tool(
    tool: dict[str, Any],
    *,
    namespace: str = "",
    param: str = "",
) -> list[dict[str, Any]] | None:
    """Normalize a single tool declaration.

    Returns ``None`` for tools that should be silently dropped.
    Returns a list because namespace flattening can produce multiple tools.
    """
    tool_type: str = tool.get("type", "")

    if not tool_type:
        raise ToolNormalizationError(
            f"{param}.type 不能为空",
            param=f"{param}.type",
        )

    if tool_type in _UNSUPPORTED_TOOL_TYPES:
        raise ToolNormalizationError(
            f"Build API 不支持工具类型: {tool_type}",
            param=param,
        )

    # Hosted tools pass through as-is
    if tool_type in _HOSTED_TOOL_TYPES:
        return [copy.deepcopy(tool)]

    # Web search variants → function
    if tool_type in _WEB_SEARCH_VARIANTS:
        return [_normalize_web_search(tool, tool_type)]

    # Function tools: passthrough with parameter root normalization
    if tool_type == "function":
        return [_normalize_function(tool, namespace, param)]

    # Namespace tools: flatten children
    if tool_type == "namespace":
        return _normalize_namespace(tool, param)

    # tool_search → drop (server eager-loads; client handled by caller)
    if tool_type == "tool_search":
        return None

    # Custom / MCP / Shell / local_shell / apply_patch → function
    if tool_type in _NORMALIZED_TOOL_TYPES:
        return [_normalize_to_function(tool, tool_type)]

    # Additional native types → function
    if tool_type in _NATIVE_TOOL_TYPES:
        return [_normalize_to_function(tool, tool_type)]

    # x_search passthrough
    if tool_type == "x_search":
        return [copy.deepcopy(tool)]

    raise ToolNormalizationError(
        f"不支持的工具类型: {tool_type}",
        param=param,
    )


def _normalize_function(
    tool: dict[str, Any],
    namespace: str,
    param: str,
) -> dict[str, Any]:
    """Normalize a function tool — parameter root nullable fix + namespace prefix."""
    name: str = tool.get("name", "")
    if not name or not name.strip():
        raise ToolNormalizationError(
            f"{param}.name 不能为空",
            param=f"{param}.name",
        )

    converted = copy.deepcopy(tool)

    # Normalize parameters root: remove nullable types
    if "parameters" in converted:
        normalized_params, changed = _normalize_parameters_root(converted["parameters"])
        if changed:
            converted["parameters"] = normalized_params

    # Namespace prefixing
    if namespace:
        converted["name"] = f"{namespace}__{name}"

    # Strip defer_loading if present
    converted.pop("defer_loading", None)

    return converted


def _normalize_namespace(tool: dict[str, Any], param: str) -> list[dict[str, Any]]:
    """Flatten a namespace tool into individual function tools.

    Each child gets prefixed with ``{namespace}__{child_name}``.
    """
    namespace: str = tool.get("name", "")
    if not namespace or not namespace.strip():
        raise ToolNormalizationError(
            f"{param}.name 不能为空",
            param=f"{param}.name",
        )

    children = tool.get("tools")
    if not isinstance(children, list):
        raise ToolNormalizationError(
            f"{param}.tools 必须是数组",
            param=f"{param}.tools",
        )

    result: list[dict[str, Any]] = []
    for index, raw_child in enumerate(children):
        child_param = f"{param}.tools[{index}]"
        if not isinstance(raw_child, dict):
            raise ToolNormalizationError(
                f"{child_param} 必须是对象",
                param=child_param,
            )
        child = dict(raw_child)  # copy to satisfy type checker invariance
        child_type = child.get("type", "")
        if child_type != "function":
            raise ToolNormalizationError(
                "namespace.tools 只能包含 function 工具",
                param=f"{child_param}.type",
            )
        result.append(_normalize_function(child, namespace, child_param))

    return result


def _normalize_web_search(
    tool: dict[str, Any],
    variant: str,
) -> dict[str, Any]:
    """Normalize a web_search tool to function type."""
    converted = copy.deepcopy(tool)
    converted["type"] = "function"
    converted.setdefault("name", "web_search")
    converted.setdefault("description", f"Search the web ({variant})")
    converted.setdefault("parameters", {"type": "object", "properties": {}})
    return converted


def _normalize_to_function(
    tool: dict[str, Any],
    original_type: str,
) -> dict[str, Any]:
    """Convert a non-standard tool to function type."""
    fn = tool.get("function")
    if isinstance(fn, dict):
        name: str = fn.get("name", "")
        description: str = fn.get("description", "")
        parameters: dict[str, Any] | Any = fn.get("parameters", {})
    else:
        name = ""
        description = ""
        parameters = {}

    if not name:
        name = tool.get("name", f"_{original_type}")
    if not description:
        description = f"Build {original_type} tool"
    if not parameters:
        parameters = tool.get("parameters", {"type": "object", "properties": {}})

    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": parameters,
    }


def _normalize_parameters_root(value: Any) -> tuple[Any, bool]:
    """Remove root-level nullability from function parameter schemas.

    Build requires the parameter root to be an object type.
    Codex can emit ``{"type": ["object", "null"]}`` or
    ``{"anyOf": [{"type": "object"}, {"type": "null"}]}``.
    """
    if not isinstance(value, dict):
        return value, False

    normalized: dict[str, Any] = dict(value)
    changed = False

    # Handle array-typed root: ["object", "null"] → "object"
    raw_type = normalized.get("type")
    if isinstance(raw_type, list):
        filtered = [t for t in raw_type if t != "null"]
        if len(filtered) != len(raw_type):
            changed = True
            if not filtered:
                return value, False
            if len(filtered) == 1:
                normalized["type"] = filtered[0]
            else:
                normalized["type"] = filtered

    # Handle anyOf/oneOf with null branches
    for keyword in ("anyOf", "oneOf"):
        branches = normalized.get(keyword)
        if not isinstance(branches, list):
            continue
        filtered = [b for b in branches if not _is_null_only_schema(b)]
        if len(filtered) == len(branches):
            continue
        changed = True
        if not filtered:
            return value, False
        if len(filtered) == 1:
            normalized[keyword] = filtered[0]
        else:
            normalized[keyword] = filtered
        normalized.setdefault("type", "object")

    return normalized, changed


def _is_null_only_schema(value: Any) -> bool:
    """Check if a schema value represents only ``null``."""
    if not isinstance(value, dict):
        return False
    raw_type = value.get("type")
    if raw_type == "null":
        return True
    if isinstance(raw_type, list) and all(t == "null" for t in raw_type):
        return True
    return False


def _tool_name(tool: dict[str, Any]) -> str:
    """Extract the effective name of a normalized tool."""
    return tool.get("name", "")
