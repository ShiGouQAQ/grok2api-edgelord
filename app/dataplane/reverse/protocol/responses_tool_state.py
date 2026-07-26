"""Tool state tracking for Build API.

Port of Go cli/responses_tool_state.go.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HostedToolChoice:
    """HostedToolChoice represents a selected hosted tool (web_search, x_search, etc.)."""

    name: str
    type: str = "hosted"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolState:
    """ToolState tracks which tools are enabled for a Build request.

    Port of Go's hostedToolChoice tracking.
    """

    hosted_tools: list[HostedToolChoice] = field(default_factory=list)

    @classmethod
    def from_tool_choice(cls, tool_choice: str | dict[str, Any] | None) -> "ToolState":
        state = cls()
        if isinstance(tool_choice, dict) and tool_choice.get("type") == "hosted":
            state.hosted_tools.append(
                HostedToolChoice(
                    name=tool_choice.get("name", ""),
                    extra={
                        k: v
                        for k, v in tool_choice.items()
                        if k not in ("type", "name")
                    },
                )
            )
        return state

    def has_hosted_tool(self, name: str) -> bool:
        return any(t.name == name for t in self.hosted_tools)

    def add_hosted_tool(self, name: str, **extra: Any) -> None:
        self.hosted_tools.append(HostedToolChoice(name=name, extra=extra))
