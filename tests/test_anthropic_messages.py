"""Anthropic message parsing unit tests.

Tests _extract_system_text() and _parse_anthropic_messages() for inline
role=system extraction from the messages array, ported from Go commit
982e27b (Claude Code injects system messages mid-conversation).
"""

import pytest
from app.products.anthropic.messages import (
    _extract_system_text,
    _parse_anthropic_messages,
)


class TestExtractSystemText:
    """Tests for _extract_system_text()."""

    def test_none_returns_empty(self):
        """None → empty string."""
        assert _extract_system_text(None) == ""

    def test_string_returns_as_is(self):
        """String → returned unchanged."""
        result = _extract_system_text("You are a helpful assistant.")
        assert result == "You are a helpful assistant."

    def test_empty_string_returns_empty(self):
        """Empty string → empty string."""
        assert _extract_system_text("") == ""

    def test_list_of_text_blocks(self):
        """List of text blocks → joined with newline."""
        blocks = [
            {"type": "text", "text": "Block 1"},
            {"type": "text", "text": "Block 2"},
        ]
        result = _extract_system_text(blocks)
        assert result == "Block 1\nBlock 2"

    def test_list_mixed_types(self):
        """List with non-text blocks → only text blocks extracted."""
        blocks = [
            {"type": "text", "text": "Keep this"},
            {"type": "image", "source": {"type": "base64", "data": "abc"}},
            {"type": "text", "text": "Keep this too"},
        ]
        result = _extract_system_text(blocks)
        assert result == "Keep this\nKeep this too"

    def test_list_returns_empty_for_no_text_blocks(self):
        """List with no text blocks → empty string."""
        blocks = [{"type": "image", "source": {"type": "base64", "data": "abc"}}]
        assert _extract_system_text(blocks) == ""

    def test_dict_returns_text_key(self):
        """Dict → returns 'text' key."""
        system = {"text": "You are Grok."}
        result = _extract_system_text(system)
        assert result == "You are Grok."

    def test_dict_missing_text_returns_empty_str(self):
        """Dict without 'text' key → returns '' (str({}.get('text', '')))."""
        result = _extract_system_text({"type": "text"})
        assert result == ""

    def test_empty_list_returns_empty(self):
        """Empty list → empty string."""
        assert _extract_system_text([]) == ""

    def test_integer_returns_str(self):
        """Integer → string representation."""
        result = _extract_system_text(42)
        assert result == "42"


class TestParseAnthropicMessages:
    """Tests for _parse_anthropic_messages()."""

    def test_top_level_system_only(self):
        """Top-level system with no inline role=system."""
        messages = [
            {"role": "user", "content": "Hello"},
        ]
        result = _parse_anthropic_messages(messages, "You are Grok.")
        assert len(result) == 2
        assert result[0] == {"role": "system", "content": "You are Grok."}
        assert result[1]["role"] == "user"

    def test_inline_system_only(self):
        """Inline role=system in messages, no top-level system."""
        messages = [
            {"role": "system", "content": "You are Claude."},
            {"role": "user", "content": "Hi"},
        ]
        result = _parse_anthropic_messages(messages, None)
        assert len(result) == 2
        assert result[0] == {"role": "system", "content": "You are Claude."}
        assert result[1]["role"] == "user"

    def test_both_top_level_and_inline_system(self):
        """Both top-level and inline system → merged with double newline."""
        messages = [
            {"role": "system", "content": "Inline instructions."},
            {"role": "user", "content": "Hello"},
        ]
        result = _parse_anthropic_messages(messages, "Top-level system.")
        assert len(result) == 2
        assert result[0] == {
            "role": "system",
            "content": "Top-level system.\n\nInline instructions.",
        }
        assert result[1]["role"] == "user"

    def test_multiple_inline_system_messages(self):
        """Multiple inline role=system messages → all accumulated."""
        messages = [
            {"role": "system", "content": "First system."},
            {"role": "user", "content": "Hello"},
            {"role": "system", "content": "Second system."},
            {"role": "assistant", "content": "Response"},
        ]
        result = _parse_anthropic_messages(messages, None)
        assert len(result) == 3
        assert result[0] == {
            "role": "system",
            "content": "First system.\n\nSecond system.",
        }
        assert result[1]["role"] == "user"
        assert result[2]["role"] == "assistant"

    def test_no_system_at_all(self):
        """No system at all → no system message prepended."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result = _parse_anthropic_messages(messages, None)
        assert len(result) == 2
        assert all(m["role"] != "system" for m in result)

    def test_empty_system_text_skipped(self):
        """Empty or whitespace-only system text → not added."""
        messages = [
            {"role": "system", "content": "   "},
            {"role": "user", "content": "Hello"},
        ]
        result = _parse_anthropic_messages(messages, None)
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_top_level_system_empty_string_skipped(self):
        """Top-level system is empty string → not added (even with inline system)."""
        messages = [
            {"role": "system", "content": "   "},
            {"role": "user", "content": "Hello"},
        ]
        result = _parse_anthropic_messages(messages, "")
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_inline_system_list_content(self):
        """Inline system content as list of text blocks."""
        messages = [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "Rule 1: Be helpful."},
                ],
            },
            {"role": "user", "content": "OK"},
        ]
        result = _parse_anthropic_messages(messages, None)
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert "Rule 1: Be helpful." in result[0]["content"]

    def test_non_system_messages_preserved_in_order(self):
        """Non-system messages preserve their original order."""
        messages = [
            {"role": "user", "content": "First"},
            {"role": "assistant", "content": "Response"},
            {"role": "user", "content": "Second"},
        ]
        result = _parse_anthropic_messages(messages, None)
        assert len(result) == 3
        assert result[0]["content"] == "First"
        assert result[1]["content"] == "Response"
        assert result[2]["content"] == "Second"

    def test_mixed_inline_system_preserves_order(self):
        """Inline system messages extracted, non-system order preserved."""
        messages = [
            {"role": "system", "content": "System A"},
            {"role": "user", "content": "User 1"},
            {"role": "assistant", "content": "Assistant 1"},
            {"role": "system", "content": "System B"},
            {"role": "user", "content": "User 2"},
        ]
        result = _parse_anthropic_messages(messages, None)
        assert len(result) == 4
        assert result[0]["role"] == "system"
        assert "System A" in result[0]["content"]
        assert "System B" in result[0]["content"]
        assert result[1] == {"role": "user", "content": "User 1"}
        assert result[2] == {"role": "assistant", "content": "Assistant 1"}
        assert result[3] == {"role": "user", "content": "User 2"}

    # ------------------------------------------------------------------
    # Edge cases — unknown role, system list blocks, inline+top-level merge
    # ------------------------------------------------------------------

    def test_unknown_role_preserved(self):
        """Unknown role like 'developer' → passed through, not dropped."""
        messages = [
            {"role": "developer", "content": "Set temperature to 0."},
            {"role": "user", "content": "Hello"},
        ]
        result = _parse_anthropic_messages(messages, None)
        assert len(result) == 2
        assert result[0]["role"] == "developer"
        assert result[0]["content"] == "Set temperature to 0."
        assert result[1]["role"] == "user"

    def test_unknown_role_mixed_with_system(self):
        """Unknown role interleaved with system messages — order preserved."""
        messages = [
            {"role": "system", "content": "Be helpful."},
            {"role": "developer", "content": "Use JSON."},
            {"role": "user", "content": "Hi"},
        ]
        result = _parse_anthropic_messages(messages, None)
        assert len(result) == 3
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "developer"
        assert result[2]["role"] == "user"

    def test_inline_system_list_text_blocks(self):
        """Inline system with content as list of text blocks (Anthropic format)."""
        messages = [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "Rule 1: Be concise."},
                    {"type": "text", "text": "Rule 2: Use markdown."},
                ],
            },
            {"role": "user", "content": "OK"},
        ]
        result = _parse_anthropic_messages(messages, None)
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert "Rule 1: Be concise." in result[0]["content"]
        assert "Rule 2: Use markdown." in result[0]["content"]

    def test_inline_system_with_non_text_blocks_skipped(self):
        """Inline system list with non-text blocks → only text blocks extracted."""
        messages = [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "Keep this."},
                    {"type": "image", "source": {"type": "url", "url": "x"}},
                ],
            },
            {"role": "user", "content": "OK"},
        ]
        result = _parse_anthropic_messages(messages, None)
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert "Keep this." in result[0]["content"]

    def test_top_level_empty_string_with_inline_system(self):
        """Top-level system='', inline system present → inline only."""
        messages = [
            {"role": "system", "content": "Inline rule."},
            {"role": "user", "content": "Hello"},
        ]
        result = _parse_anthropic_messages(messages, "")
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "Inline rule."

    def test_top_level_whitespace_with_inline_system(self):
        """Top-level system with whitespace, inline system → inline only."""
        messages = [
            {"role": "system", "content": "Inline."},
            {"role": "user", "content": "Hello"},
        ]
        result = _parse_anthropic_messages(messages, "   ")
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "Inline."

    def test_system_with_list_content_empty_text_blocks(self):
        """System content as list of empty text blocks → filtered out."""
        messages = [
            {"role": "system", "content": [{"type": "text", "text": ""}]},
            {"role": "user", "content": "Hello"},
        ]
        result = _parse_anthropic_messages(messages, None)
        assert len(result) == 1
        assert result[0]["role"] == "user"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
