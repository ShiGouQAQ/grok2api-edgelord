"""Anthropic message parsing unit tests.

Tests _extract_system_text(), _parse_anthropic_messages(), _build_search_content_blocks(),
and _build_message_response() for cache tokens and web search usage fields.
"""

import pytest
from app.products.anthropic.messages import (
    _extract_system_text,
    _parse_anthropic_messages,
    _build_search_content_blocks,
    _build_message_response,
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


# ------------------------------------------------------------------
# _build_search_content_blocks tests
# ------------------------------------------------------------------


class TestBuildSearchContentBlocks:
    def test_empty_sources_returns_empty(self):
        assert _build_search_content_blocks([]) == []

    def test_returns_server_tool_use_and_web_search_tool_result(self):
        sources = [{"url": "https://a.com", "title": "A"}]
        blocks = _build_search_content_blocks(sources)
        assert len(blocks) == 2
        assert blocks[0]["type"] == "server_tool_use"
        assert blocks[0]["name"] == "web_search"
        assert blocks[1]["type"] == "web_search_tool_result"
        assert blocks[1]["tool_use_id"] == blocks[0]["id"]

    def test_tool_use_includes_query(self):
        sources = [{"url": "https://a.com", "title": "A"}]
        blocks = _build_search_content_blocks(sources, query="python")
        assert blocks[0]["input"] == {"query": "python"}

    def test_tool_use_empty_input_when_no_query(self):
        sources = [{"url": "https://a.com", "title": "A"}]
        blocks = _build_search_content_blocks(sources)
        assert blocks[0]["input"] == {}

    def test_deduplicates_urls(self):
        sources = [
            {"url": "https://a.com", "title": "A1"},
            {"url": "https://a.com", "title": "A2"},
            {"url": "https://b.com", "title": "B"},
        ]
        blocks = _build_search_content_blocks(sources)
        hits = blocks[1]["content"]
        assert len(hits) == 2
        urls = [h["url"] for h in hits]
        assert urls == ["https://a.com", "https://b.com"]

    def test_empty_url_skipped(self):
        sources = [
            {"url": "", "title": "No URL"},
            {"url": "https://a.com", "title": "A"},
        ]
        blocks = _build_search_content_blocks(sources)
        hits = blocks[1]["content"]
        assert len(hits) == 1
        assert hits[0]["url"] == "https://a.com"

    def test_result_block_fields(self):
        sources = [{"url": "https://x.com", "title": "X Page"}]
        blocks = _build_search_content_blocks(sources)
        hit = blocks[1]["content"][0]
        assert hit["type"] == "web_search_result"
        assert hit["title"] == "X Page"
        assert hit["url"] == "https://x.com"

    def test_title_falls_back_to_url(self):
        sources = [{"url": "https://example.com"}]
        blocks = _build_search_content_blocks(sources)
        hit = blocks[1]["content"][0]
        assert hit["title"] == "https://example.com"

    def test_all_empty_urls_returns_error_block(self):
        sources = [{"url": "", "title": "No URL"}, {"url": "", "title": "Also no"}]
        blocks = _build_search_content_blocks(sources)
        assert len(blocks) == 2
        assert blocks[1]["content"]["type"] == "web_search_tool_result_error"
        assert blocks[1]["content"]["error_code"] == "unavailable"

    def test_multiple_sources_all_unique(self):
        sources = [
            {"url": f"https://example.com/{i}", "title": f"Page {i}"} for i in range(5)
        ]
        blocks = _build_search_content_blocks(sources)
        hits = blocks[1]["content"]
        assert len(hits) == 5


# ------------------------------------------------------------------
# _build_message_response tests
# ------------------------------------------------------------------


class TestBuildMessageResponse:
    def test_basic_fields(self):
        resp = _build_message_response("msg_1", "grok-4", [], "end_turn", 10, 20)
        assert resp["id"] == "msg_1"
        assert resp["type"] == "message"
        assert resp["role"] == "assistant"
        assert resp["model"] == "grok-4"
        assert resp["stop_reason"] == "end_turn"
        assert resp["stop_sequence"] is None

    def test_usage_includes_cache_tokens(self):
        resp = _build_message_response(
            "m", "g", [], "end_turn", 10, 20, cache_read=5, cache_creation=3
        )
        usage = resp["usage"]
        assert usage["input_tokens"] == 10
        assert usage["output_tokens"] == 20
        assert usage["cache_creation_input_tokens"] == 3
        assert usage["cache_read_input_tokens"] == 5

    def test_usage_cache_tokens_default_zero(self):
        resp = _build_message_response("m", "g", [], "end_turn", 10, 20)
        usage = resp["usage"]
        assert usage["cache_creation_input_tokens"] == 0
        assert usage["cache_read_input_tokens"] == 0

    def test_web_search_requests_in_usage(self):
        resp = _build_message_response(
            "m", "g", [], "end_turn", 10, 20, web_search_requests=3
        )
        assert resp["usage"]["server_tool_use"]["web_search_requests"] == 3

    def test_no_web_search_requests_key_when_zero(self):
        resp = _build_message_response(
            "m", "g", [], "end_turn", 10, 20, web_search_requests=0
        )
        assert "server_tool_use" not in resp["usage"]

    def test_content_passthrough(self):
        content = [{"type": "text", "text": "hello"}]
        resp = _build_message_response("m", "g", content, "end_turn", 10, 20)
        assert resp["content"] == content

    def test_tool_use_stop_reason(self):
        resp = _build_message_response("m", "g", [], "tool_use", 10, 20)
        assert resp["stop_reason"] == "tool_use"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
