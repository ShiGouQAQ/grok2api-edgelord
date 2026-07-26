"""Reasoning decode failure recovery for Build API.

Port of Go cli/responses_reasoning_recovery.go.
Handles opaque reasoning decode failures by stripping encrypted content and retrying.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def recover_reasoning_decode_failure(
    body: dict[str, Any],
    prompt_cache_key: str | None,
) -> tuple[dict[str, Any], str | None]:
    """recover_reasoning_decode_failure attempts to recover from reasoning decode failure.

    Strategy:
    1. Strip encrypted_content from reasoning items
    2. Clear prompt_cache_key to reset session identity
    3. Return warning about the recovery

    Returns (modified_body, warning_header_value).
    """
    modified = False
    output = body.get("output", [])
    new_output: list[dict[str, Any]] = []

    warning_parts: list[str] = []

    for item in output:
        if isinstance(item, dict) and item.get("type") == "reasoning":
            encrypted = item.get("encrypted_content", "")
            if encrypted:
                item = dict(item)
                item.pop("encrypted_content", None)
                item["recovered"] = True
                modified = True
                warning_parts.append("reasoning-recovered")

        new_output.append(item)

    if modified:
        result = dict(body)
        result["output"] = new_output

        if prompt_cache_key:
            result.pop("prompt_cache_key", None)
            warning_parts.append("cache-cleared")

        warning = "; ".join(warning_parts) if warning_parts else "reasoning-recovered"
        return result, warning

    return body, None


def strip_reasoning_encrypted_content(body: dict[str, Any]) -> dict[str, Any]:
    """strip_reasoning_encrypted_content removes all encrypted_content from reasoning items."""
    output = body.get("output", [])
    new_output: list[dict[str, Any]] = []

    for item in output:
        if isinstance(item, dict) and item.get("type") == "reasoning":
            item = dict(item)
            item.pop("encrypted_content", None)
        new_output.append(item)

    result = dict(body)
    result["output"] = new_output
    return result
