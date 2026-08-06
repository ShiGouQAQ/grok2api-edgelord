import pytest
from app.dataplane.reverse.protocol.responses_x_search_filter import (
    build_x_search_response_filter,
    _is_internal_x_search_item,
)
from app.dataplane.reverse.protocol.responses_cache_route import (
    prepare_build_prompt_cache_route,
    _is_cache_capable,
)

# --- x_search filter ---


def test_filter_drops_xs_call():
    output = [
        {"type": "custom_tool_call", "call_id": "xs_call_123", "name": "x_user_search"}
    ]
    filter_fn = build_x_search_response_filter()
    result = filter_fn(output)
    assert len(result) == 0


def test_filter_passthrough_normal():
    output = [{"type": "function_call", "name": "test", "arguments": "{}"}]
    filter_fn = build_x_search_response_filter()
    result = filter_fn(output)
    assert len(result) == 1


def test_filter_reindexes():
    output = [
        {"type": "custom_tool_call", "call_id": "xs_call_1", "index": 0},
        {"type": "message", "role": "assistant", "content": [], "index": 1},
    ]
    filter_fn = build_x_search_response_filter()
    result = filter_fn(output)
    assert len(result) == 1
    assert result[0]["index"] == 0  # reindexed


def test_filter_empty():
    filter_fn = build_x_search_response_filter()
    assert filter_fn([]) == []


def test_is_internal_x_search_item():
    assert _is_internal_x_search_item(
        {"type": "custom_tool_call", "call_id": "xs_call_abc"}
    )


def test_is_internal_x_search_item_by_name():
    assert _is_internal_x_search_item(
        {"type": "custom_tool_call", "name": "x_user_search"}
    )


def test_is_internal_x_search_item_non_match():
    assert not _is_internal_x_search_item(
        {"type": "custom_tool_call", "name": "web_search"}
    )


# --- Cache route ---


def test_cache_route_adds_search_tools():
    body = {"model": "grok-4.5", "input": []}
    result, added = prepare_build_prompt_cache_route(
        body, "responses", "grok-4.5", "test-key"
    )
    assert added is True
    types = [t["type"] for t in result["tools"]]
    assert "web_search" in types
    assert "x_search" in types


def test_cache_route_no_key():
    result, added = prepare_build_prompt_cache_route(
        {"model": "grok-4.5"}, "responses", "grok-4.5", None
    )
    assert added is False


def test_cache_route_uncapable_operation():
    result, added = prepare_build_prompt_cache_route(
        {"model": "grok-4.5"}, "video", "grok-4.5", "key"
    )
    assert added is False


def test_cache_route_uncapable_model():
    result, added = prepare_build_prompt_cache_route(
        {"model": "grok-imagine"}, "responses", "grok-imagine", "key"
    )
    assert added is False


def test_is_cache_capable():
    assert _is_cache_capable("responses", "grok-4.5")
    assert not _is_cache_capable("video", "grok-4.5")
    assert _is_cache_capable("chat", "grok-4.5-build-free")


def test_cache_route_existing_tools_not_duplicated():
    body = {"model": "grok-4.5", "tools": [{"type": "web_search"}]}
    result, added = prepare_build_prompt_cache_route(
        body, "responses", "grok-4.5", "key"
    )
    types = [t["type"] for t in result["tools"]]
    assert types.count("web_search") == 1  # not duplicated
