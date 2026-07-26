"""Prompt cache routing for Build API.

Port of Go cli/responses_cache_route.go.
Injects web_search and x_search tools for cache-capable paths.
"""

from typing import Any

# Cache-capable operations
_CACHE_CAPABLE_OPERATIONS: frozenset[str] = frozenset(
    {
        "responses",
        "chat",
        "compact",
    }
)

# Models that support cache routing
_CACHE_CAPABLE_MODEL_PREFIXES: tuple[str, ...] = (
    "grok-4.",
    "grok-build",
)


def prepare_build_prompt_cache_route(
    body: dict[str, Any],
    operation: str,
    model: str,
    prompt_cache_key: str | None,
) -> tuple[dict[str, Any], bool]:
    """prepare_build_prompt_cache_route injects search tools for cache-capable paths.

    Returns (modified_body, has_tools_added).
    """
    if not _is_cache_capable(operation, model):
        return body, False

    if prompt_cache_key is None:
        return body, False

    # Inject web_search and x_search tools if not already present
    existing_tools = body.get("tools", [])
    existing_types = {t.get("type") for t in existing_tools if isinstance(t, dict)}

    added = False
    if "web_search" not in existing_types:
        existing_tools.append(
            {"type": "web_search", "enable_image_understanding": True}
        )
        added = True
    if "x_search" not in existing_types:
        existing_tools.append({"type": "x_search", "enable_video_understanding": True})
        added = True

    if added:
        body = dict(body)
        body["tools"] = existing_tools

    return body, added


def _is_cache_capable(operation: str, model: str) -> bool:
    """_is_cache_capable checks if the operation+model supports cache routing."""
    if operation not in _CACHE_CAPABLE_OPERATIONS:
        return False
    return any(model.startswith(prefix) for prefix in _CACHE_CAPABLE_MODEL_PREFIXES)


def build_prompt_cache_route(
    operation: str,
    model: str,
    prompt_cache_key: str | None,
) -> str | None:
    """build_prompt_cache_route returns the cache route for the given operation.

    Returns None if caching is not applicable.
    """
    if not _is_cache_capable(operation, model):
        return None
    return operation
