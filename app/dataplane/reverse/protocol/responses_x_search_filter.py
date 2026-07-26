"""x_search response output filter for Build API.

Port of Go cli/responses_x_search_filter.go.
Filters internal x_search subcalls from the response output.
"""

from typing import Any, Callable

# x_search call IDs and names that indicate internal subcalls to filter out
_XS_CALL_PREFIXES: tuple[str, ...] = (
    "xs_call",
    "xs_web",
)
_XS_CALL_NAMES: frozenset[str] = frozenset(
    {
        "x_user_search",
        "x_semantic_search",
        "x_keyword_search",
        "x_thread_fetch",
        "x_user_mentions_search",
    }
)


def build_x_search_response_filter() -> Callable[
    [list[dict[str, Any]]], list[dict[str, Any]]
]:
    """build_x_search_response_filter returns a filter function that removes internal x_search items.

    The filter drops custom_tool_call items that represent internal
    x_search subcalls, which should not be visible to the client.
    """

    def _filter_output(output: list[dict[str, Any]]) -> list[dict[str, Any]]:
        filtered: list[dict[str, Any]] = []
        for item in output:
            if _is_internal_x_search_item(item):
                continue
            filtered.append(item)

        # Reindex output indices
        for idx, item in enumerate(filtered):
            if "index" in item:
                item["index"] = idx

        return filtered

    return _filter_output


def _is_internal_x_search_item(item: dict[str, Any]) -> bool:
    """_is_internal_x_search_item checks if an output item is an internal x_search subcall."""
    if not isinstance(item, dict):
        return False

    item_type = item.get("type", "")
    if item_type != "custom_tool_call":
        return False

    # Check call_id prefix
    call_id = item.get("call_id", "")
    if any(call_id.startswith(prefix) for prefix in _XS_CALL_PREFIXES):
        return True

    # Check name
    name = item.get("name", "")
    if name in _XS_CALL_NAMES:
        return True

    return False
