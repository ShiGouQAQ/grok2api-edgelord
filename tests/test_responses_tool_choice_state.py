import pytest
from app.dataplane.reverse.protocol.responses_tool_choice import normalize_tool_choice
from app.dataplane.reverse.protocol.responses_tool_state import (
    ToolState,
    HostedToolChoice,
)

# --- Tool Choice ---


def test_tc_none():
    assert normalize_tool_choice(None) is None


def test_tc_auto():
    assert normalize_tool_choice("auto") == "auto"


def test_tc_none_str():
    assert normalize_tool_choice("none") == "none"


def test_tc_required():
    assert normalize_tool_choice("required") == "required"


def test_tc_function_dict():
    result = normalize_tool_choice({"type": "function", "name": "test"})
    assert isinstance(result, dict)
    assert result["type"] == "function"


def test_tc_custom_to_function():
    result = normalize_tool_choice({"type": "custom", "name": "my-tool"})
    assert isinstance(result, dict)
    assert result["type"] == "function"


def test_tc_hosted_to_auto():
    result = normalize_tool_choice({"type": "hosted", "name": "web_search"})
    assert result == "auto"


def test_tc_unknown_string_default():
    assert normalize_tool_choice("something") == "auto"


# --- Tool State ---


def test_ts_empty():
    state = ToolState()
    assert len(state.hosted_tools) == 0


def test_ts_from_hosted_choice():
    state = ToolState.from_tool_choice({"type": "hosted", "name": "web_search"})
    assert state.has_hosted_tool("web_search")


def test_ts_from_non_hosted_choice():
    state = ToolState.from_tool_choice("auto")
    assert len(state.hosted_tools) == 0


def test_ts_add_hosted():
    state = ToolState()
    state.add_hosted_tool("x_search", enable_video=True)
    assert state.has_hosted_tool("x_search")


def test_ts_has_hosted():
    state = ToolState(hosted_tools=[HostedToolChoice(name="web_search")])
    assert state.has_hosted_tool("web_search")
    assert not state.has_hosted_tool("x_search")


def test_hosted_tool_choice_extra():
    htc = HostedToolChoice(
        name="web_search", extra={"enable_image_understanding": True}
    )
    assert htc.extra["enable_image_understanding"] is True
