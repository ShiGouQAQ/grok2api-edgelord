"""Tool choice normalization for Build API.

Port of Go cli/responses_tool_choice.go.
"""

from typing import Any

# Supported hosted tool choices
_HOSTED_TOOL_CHOICES: frozenset[str] = frozenset(
    {
        "web_search",
        "x_search",
        "image_generation",
    }
)


def normalize_tool_choice(
    tool_choice: str | dict[str, Any] | None,
) -> str | dict[str, Any] | None:
    """normalize_tool_choice normalizes tool_choice for the Build API.

    Handles:
    - None → None (default, let upstream decide)
    - "auto" / "none" / "required" → pass through as-is
    - {"type": "function", "name": "..."} → pass through as-is
    - {"type": "custom", "name": "..."} → convert to function format
    - {"type": "hosted", "name": "web_search"} → convert to "auto"
    """
    if tool_choice is None:
        return None

    if isinstance(tool_choice, str):
        if tool_choice in ("auto", "none", "required"):
            return tool_choice
        return "auto"

    if isinstance(tool_choice, dict):
        tc_type = tool_choice.get("type", "")
        tc_name = tool_choice.get("name", "")

        # Function type → pass through
        if tc_type == "function":
            return tool_choice

        # Custom type → convert to function
        if tc_type == "custom" and tc_name:
            return {
                "type": "function",
                "name": tc_name,
                "function": tool_choice.get("function", {}),
            }

        # Hosted tool → auto
        if tc_type == "hosted" and tc_name in _HOSTED_TOOL_CHOICES:
            return "auto"

        # Specific function or tool name → pass through or auto
        if tc_name:
            return {"type": "function", "name": tc_name}

    return "auto"
