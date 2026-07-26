"""Codex tool bridges for Build API.

Port of Go cli/responses_codex_tools.go.
Handles shell, apply_patch, and legacy local_shell tools used by Codex/Claude Code.
"""

from __future__ import annotations

from typing import Any


def normalize_shell_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """normalize_shell_tool normalizes a shell/bash command tool.

    Shell tools execute commands in a sandboxed environment.
    """
    fn = tool.get("function", {})
    name = fn.get("name", tool.get("name", "shell"))
    description = fn.get(
        "description", tool.get("description", "Execute a shell command")
    )
    parameters = fn.get(
        "parameters",
        tool.get(
            "parameters",
            {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The command to execute",
                    },
                },
                "required": ["command"],
            },
        ),
    )

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


def normalize_apply_patch_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """normalize_apply_patch_tool normalizes a patch application tool."""
    fn = tool.get("function", {})
    name = fn.get("name", tool.get("name", "apply_patch"))
    description = fn.get(
        "description", tool.get("description", "Apply a patch to the codebase")
    )
    parameters = fn.get(
        "parameters",
        tool.get(
            "parameters",
            {
                "type": "object",
                "properties": {
                    "patch": {
                        "type": "string",
                        "description": "The diff/patch content",
                    },
                },
                "required": ["patch"],
            },
        ),
    )

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


def normalize_legacy_local_shell_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """normalize_legacy_local_shell_tool converts legacy local_shell to current shell format."""
    fn = tool.get("function", {})
    name = fn.get("name", tool.get("name", "local_shell"))

    result = normalize_shell_tool(tool)
    result["function"]["name"] = name
    return result
