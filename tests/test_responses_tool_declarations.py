"""Tests for responses_tool_declarations — Build API tool normalization.

Port of Go cli/responses_tool_declarations.go.
"""

from __future__ import annotations

import copy
import pytest
from typing import Any

from app.dataplane.reverse.protocol.responses_tool_declarations import (
    ToolNormalizationError,
    build_hosted_tool_declarations,
    normalize_responses_tools,
    _HOSTED_TOOL_TYPES,
    _NORMALIZED_TOOL_TYPES,
    _normalize_parameters_root,
    _is_null_only_schema,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_web_search_in_hosted():
    assert "web_search" in _HOSTED_TOOL_TYPES


def test_x_search_in_hosted():
    assert "x_search" in _HOSTED_TOOL_TYPES


def test_image_generation_in_hosted():
    assert "image_generation" in _HOSTED_TOOL_TYPES


def test_normalized_tool_types():
    for t in ("custom", "mcp", "shell", "local_shell", "apply_patch"):
        assert t in _NORMALIZED_TOOL_TYPES


# ---------------------------------------------------------------------------
# Empty / trivial
# ---------------------------------------------------------------------------


def test_empty_tools():
    assert normalize_responses_tools([]) == []


def test_single_function_tool_passthrough():
    tool = {
        "type": "function",
        "name": "get_weather",
        "description": "Get weather",
        "parameters": {"type": "object"},
    }
    result = normalize_responses_tools([tool])
    assert len(result) == 1
    assert result[0]["type"] == "function"
    assert result[0]["name"] == "get_weather"


# ---------------------------------------------------------------------------
# Hosted tools
# ---------------------------------------------------------------------------


def test_hosted_web_search_passthrough():
    tool = {"type": "web_search"}
    result = normalize_responses_tools([tool])
    assert len(result) == 1
    assert result[0]["type"] == "web_search"


def test_hosted_x_search_passthrough():
    tool = {"type": "x_search"}
    result = normalize_responses_tools([tool])
    assert len(result) == 1
    assert result[0]["type"] == "x_search"


def test_hosted_image_generation_passthrough():
    tool = {"type": "image_generation"}
    result = normalize_responses_tools([tool])
    assert len(result) == 1
    assert result[0]["type"] == "image_generation"


# ---------------------------------------------------------------------------
# Function tools
# ---------------------------------------------------------------------------


def test_function_tool_deep_copied():
    tool = {"type": "function", "name": "test", "parameters": {"type": "object"}}
    result = normalize_responses_tools([tool])
    result[0]["name"] = "mutated"
    assert tool["name"] == "test"


def test_function_tool_parameters_nullable_root_normalized():
    tool = {
        "type": "function",
        "name": "test",
        "parameters": {"type": ["object", "null"]},
    }
    result = normalize_responses_tools([tool])
    assert result[0]["parameters"]["type"] == "object"


def test_function_tool_defer_loading_stripped():
    tool = {"type": "function", "name": "test", "defer_loading": True}
    result = normalize_responses_tools([tool])
    assert "defer_loading" not in result[0]


def test_function_tool_empty_name_raises():
    tool = {"type": "function", "name": ""}
    with pytest.raises(ToolNormalizationError, match="name 不能为空"):
        normalize_responses_tools([tool])


def test_function_tool_missing_name_raises():
    tool = {"type": "function"}
    with pytest.raises(ToolNormalizationError, match="name 不能为空"):
        normalize_responses_tools([tool])


# ---------------------------------------------------------------------------
# Namespace tools
# ---------------------------------------------------------------------------


def test_namespace_tool_flattened():
    tool = {
        "type": "namespace",
        "name": "my_ns",
        "tools": [
            {"type": "function", "name": "sub_a", "parameters": {"type": "object"}},
            {"type": "function", "name": "sub_b"},
        ],
    }
    result = normalize_responses_tools([tool])
    assert len(result) == 2
    assert result[0]["type"] == "function"
    assert result[0]["name"] == "my_ns__sub_a"
    assert result[1]["name"] == "my_ns__sub_b"


def test_namespace_tool_empty_name_raises():
    tool = {"type": "namespace", "name": "", "tools": []}
    with pytest.raises(ToolNormalizationError, match="name 不能为空"):
        normalize_responses_tools([tool])


def test_namespace_tool_non_array_tools_raises():
    tool = {"type": "namespace", "name": "ns", "tools": "not_a_list"}
    with pytest.raises(ToolNormalizationError, match="tools 必须是数组"):
        normalize_responses_tools([tool])


def test_namespace_tool_non_function_child_raises():
    tool = {
        "type": "namespace",
        "name": "ns",
        "tools": [{"type": "web_search"}],
    }
    with pytest.raises(ToolNormalizationError, match="只能包含 function"):
        normalize_responses_tools([tool])


def test_namespace_tool_non_dict_child_raises():
    tool = {"type": "namespace", "name": "ns", "tools": ["bad"]}
    with pytest.raises(ToolNormalizationError, match="必须是对象"):
        normalize_responses_tools([tool])


# ---------------------------------------------------------------------------
# tool_search (dropped)
# ---------------------------------------------------------------------------


def test_tool_search_dropped():
    tool = {"type": "tool_search", "execution": "server"}
    result = normalize_responses_tools([tool])
    assert result == []


def test_tool_search_client_dropped():
    tool = {"type": "tool_search", "execution": "client"}
    result = normalize_responses_tools([tool])
    assert result == []


# ---------------------------------------------------------------------------
# Custom / MCP / Shell tools → function
# ---------------------------------------------------------------------------


def test_custom_tool_normalized():
    tool = {
        "type": "custom",
        "function": {"name": "my_custom", "description": "Custom"},
    }
    result = normalize_responses_tools([tool])
    assert len(result) == 1
    assert result[0]["type"] == "function"
    assert result[0]["name"] == "my_custom"


def test_mcp_tool_normalized():
    tool = {"type": "mcp", "function": {"name": "mcp_tool"}}
    result = normalize_responses_tools([tool])
    assert result[0]["type"] == "function"
    assert result[0]["name"] == "mcp_tool"


def test_shell_tool_normalized():
    tool = {"type": "shell", "name": "shell_exec"}
    result = normalize_responses_tools([tool])
    assert result[0]["type"] == "function"
    assert result[0]["name"] == "shell_exec"


def test_local_shell_tool_normalized():
    tool = {"type": "local_shell", "function": {"name": "local_cmd"}}
    result = normalize_responses_tools([tool])
    assert result[0]["type"] == "function"
    assert result[0]["name"] == "local_cmd"


def test_apply_patch_tool_normalized():
    tool = {"type": "apply_patch", "function": {"name": "apply"}}
    result = normalize_responses_tools([tool])
    assert result[0]["type"] == "function"


def test_custom_tool_no_name_fallback():
    tool = {"type": "custom"}
    result = normalize_responses_tools([tool])
    assert result[0]["type"] == "function"
    assert result[0]["name"] == "_custom"


def test_custom_tool_no_fn_no_top_name():
    tool = {"type": "custom"}
    result = normalize_responses_tools([tool])
    assert result[0]["description"] == "Build custom tool"


# ---------------------------------------------------------------------------
# Native tool types
# ---------------------------------------------------------------------------


def test_code_interpreter_normalized():
    tool = {"type": "code_interpreter", "function": {"name": "code_exec"}}
    result = normalize_responses_tools([tool])
    assert result[0]["type"] == "function"
    assert result[0]["name"] == "code_exec"


def test_file_search_normalized():
    tool = {"type": "file_search", "function": {"name": "fs"}}
    result = normalize_responses_tools([tool])
    assert result[0]["type"] == "function"


# ---------------------------------------------------------------------------
# Web search variants
# ---------------------------------------------------------------------------


def test_web_search_preview_normalized():
    tool = {"type": "web_search_preview", "name": "ws_preview"}
    result = normalize_responses_tools([tool])
    assert len(result) == 1
    assert result[0]["type"] == "function"
    assert result[0]["name"] == "ws_preview"
    assert "web_search_preview" in result[0]["description"]


def test_web_search_variant_deep_copied():
    tool = {"type": "web_search_preview_2025_03_11"}
    result = normalize_responses_tools([tool])
    result[0]["name"] = "mutated"
    assert "name" not in tool  # original unaffected


# ---------------------------------------------------------------------------
# Unsupported types
# ---------------------------------------------------------------------------


def test_computer_use_preview_raises():
    tool = {"type": "computer_use_preview"}
    with pytest.raises(ToolNormalizationError, match="不支持"):
        normalize_responses_tools([tool])


def test_empty_type_raises():
    tool = {"type": ""}
    with pytest.raises(ToolNormalizationError, match="type 不能为空"):
        normalize_responses_tools([tool])


def test_missing_type_raises():
    tool = {}
    with pytest.raises(ToolNormalizationError, match="type 不能为空"):
        normalize_responses_tools([tool])


def test_unknown_type_raises():
    tool = {"type": "some_future_tool"}
    with pytest.raises(ToolNormalizationError, match="不支持的工具类型"):
        normalize_responses_tools([tool])


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def test_duplicate_names_deduplicated():
    tools = [
        {"type": "function", "name": "test", "parameters": {"type": "object"}},
        {"type": "function", "name": "test"},
    ]
    result = normalize_responses_tools(tools)
    assert len(result) == 1


def test_different_names_not_deduplicated():
    tools = [
        {"type": "function", "name": "a"},
        {"type": "function", "name": "b"},
    ]
    result = normalize_responses_tools(tools)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# build_hosted_tool_declarations
# ---------------------------------------------------------------------------


def test_build_hosted_tool_declarations():
    hosted = build_hosted_tool_declarations()
    types = [t["type"] for t in hosted]
    assert "web_search" in types
    assert "x_search" in types
    assert hosted[0]["enable_image_understanding"] is True
    assert hosted[1]["enable_video_understanding"] is True


# ---------------------------------------------------------------------------
# Parameter root normalization
# ---------------------------------------------------------------------------


class TestNormalizeParametersRoot:
    def test_no_change_for_normal_object(self):
        params = {"type": "object", "properties": {"q": {"type": "string"}}}
        result, changed = _normalize_parameters_root(params)
        assert changed is False
        assert result == params

    def test_array_type_nullable_removed(self):
        params = {"type": ["object", "null"]}
        result, changed = _normalize_parameters_root(params)
        assert changed is True
        assert result["type"] == "object"

    def test_array_type_multiple_non_null(self):
        params = {"type": ["object", "string"]}
        result, changed = _normalize_parameters_root(params)
        assert changed is False
        assert result["type"] == ["object", "string"]

    def test_array_type_all_null_no_change(self):
        params = {"type": ["null"]}
        result, changed = _normalize_parameters_root(params)
        assert changed is False

    def test_anyof_with_null_branch(self):
        params = {"anyOf": [{"type": "object"}, {"type": "null"}]}
        result, changed = _normalize_parameters_root(params)
        assert changed is True
        assert result["anyOf"] == {"type": "object"}
        assert result["type"] == "object"

    def test_oneof_with_null_branch(self):
        params = {"oneOf": [{"type": "object"}, {"type": "null"}]}
        result, changed = _normalize_parameters_root(params)
        assert changed is True
        assert result["type"] == "object"

    def test_non_dict_passthrough(self):
        params = "not a dict"
        result, changed = _normalize_parameters_root(params)
        assert result == "not a dict"
        assert changed is False


class TestIsNullOnlySchema:
    def test_null_string(self):
        assert _is_null_only_schema({"type": "null"}) is True

    def test_null_array(self):
        assert _is_null_only_schema({"type": ["null"]}) is True

    def test_mixed_array(self):
        assert _is_null_only_schema({"type": ["null", "string"]}) is False

    def test_non_null(self):
        assert _is_null_only_schema({"type": "string"}) is False

    def test_non_dict(self):
        assert _is_null_only_schema("null") is False


# ---------------------------------------------------------------------------
# Mixed tool types
# ---------------------------------------------------------------------------


def test_mixed_hosted_function_custom():
    tools = [
        {"type": "web_search"},
        {"type": "function", "name": "my_fn"},
        {"type": "custom", "function": {"name": "my_custom"}},
    ]
    result = normalize_responses_tools(tools)
    assert len(result) == 3
    assert result[0]["type"] == "web_search"
    assert result[1]["type"] == "function"
    assert result[2]["type"] == "function"  # custom → function


def test_namespace_with_function_and_hosted():
    tools = [
        {"type": "web_search"},
        {
            "type": "namespace",
            "name": "ns",
            "tools": [{"type": "function", "name": "fn1"}],
        },
    ]
    result = normalize_responses_tools(tools)
    assert len(result) == 2
    assert result[0]["type"] == "web_search"
    assert result[1]["name"] == "ns__fn1"


# ---------------------------------------------------------------------------
# Error param tracking
# ---------------------------------------------------------------------------


def test_error_has_param():
    tool = {"type": "function", "name": ""}
    with pytest.raises(ToolNormalizationError) as exc_info:
        normalize_responses_tools([tool])
    assert exc_info.value.param == "tools[0].name"
    assert exc_info.value.code == "invalid_parameter"
