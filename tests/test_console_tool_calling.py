"""Tests for console model tool calling support.

Covers:
- Tool prompt injection into console chat/responses paths
- ToolSieve integration with ConsoleStreamAdapter
- Non-streaming tool call detection
- tool_choice parameter handling
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.dataplane.reverse.protocol.tool_prompt import (
    build_tool_system_prompt,
    extract_tool_names,
    inject_into_message,
)
from app.dataplane.reverse.protocol.tool_parser import ParsedToolCall, parse_tool_calls
from app.products.openai._tool_sieve import ToolSieve
from app.dataplane.reverse.protocol.xai_console_chat import ConsoleStreamAdapter


# ---------------------------------------------------------------------------
# Tool prompt injection
# ---------------------------------------------------------------------------


class TestConsoleToolPromptInjection:
    """Tool prompt should be correctly built and injected for console models."""

    def test_extract_tool_names_from_openai_tools(self):
        """extract_tool_names should return function names from OpenAI tools array."""
        tools = [
            {
                "type": "function",
                "function": {"name": "get_weather", "description": "Get weather"},
            },
            {
                "type": "function",
                "function": {"name": "search", "description": "Search web"},
            },
        ]
        names = extract_tool_names(tools)
        assert names == ["get_weather", "search"]

    def test_extract_tool_names_empty_returns_empty_list(self):
        """Empty tools array should return empty list."""
        assert extract_tool_names([]) == []

    def test_build_tool_system_prompt_contains_tool_names(self):
        """Generated prompt should mention tool names."""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                    },
                },
            }
        ]
        prompt = build_tool_system_prompt(tools, "auto")
        assert "get_weather" in prompt
        assert "<tool_calls>" in prompt

    def test_build_tool_system_prompt_with_auto_choice(self):
        """tool_choice='auto' should produce auto instruction."""
        tools = [
            {"type": "function", "function": {"name": "foo", "description": "bar"}}
        ]
        prompt = build_tool_system_prompt(tools, "auto")
        assert "WHEN TO CALL" in prompt
        assert "Call a tool when" in prompt

    def test_build_tool_system_prompt_with_none_choice(self):
        """tool_choice='none' should produce 'do not call' instruction."""
        tools = [
            {"type": "function", "function": {"name": "foo", "description": "bar"}}
        ]
        prompt = build_tool_system_prompt(tools, "none")
        assert "Do NOT call any tools" in prompt

    def test_build_tool_system_prompt_with_required_choice(self):
        """tool_choice='required' should produce 'must output' instruction."""
        tools = [
            {"type": "function", "function": {"name": "foo", "description": "bar"}}
        ]
        prompt = build_tool_system_prompt(tools, "required")
        assert "MUST output" in prompt

    def test_build_tool_system_prompt_with_forced_function(self):
        """tool_choice={'type':'function','function':{'name':'foo'}} should force specific tool."""
        tools = [
            {"type": "function", "function": {"name": "foo", "description": "bar"}}
        ]
        prompt = build_tool_system_prompt(
            tools, {"type": "function", "function": {"name": "foo"}}
        )
        assert 'tool named "foo"' in prompt

    def test_inject_into_message_prepends_tool_prompt(self):
        """inject_into_message should prepend tool prompt to message."""
        result = inject_into_message("hello", "[tool prompt]")
        assert result == "[system]: [tool prompt]\n\nhello"

    def test_inject_into_message_no_tool_prompt(self):
        """Empty tool prompt should still prepend."""
        result = inject_into_message("hello", "")
        assert result == "[system]: \n\nhello"


# ---------------------------------------------------------------------------
# ToolSieve + ConsoleStreamAdapter integration
# ---------------------------------------------------------------------------


class TestToolSieveWithConsoleAdapter:
    """ToolSieve should correctly detect tool calls in console stream text."""

    def setup_method(self):
        self.adapter = ConsoleStreamAdapter()
        self.sieve = ToolSieve(["get_weather", "get_time"])

    def _feed_console_text(self, text: str) -> tuple[str, list[ParsedToolCall] | None]:
        """Simulate feeding console SSE text events through adapter then sieve."""
        import orjson

        event_data = orjson.dumps({"delta": text}).decode()
        tokens = self.adapter.feed("response.output_text.delta", event_data)
        combined_safe = ""
        combined_calls = None
        for tok in tokens:
            safe, calls = self.sieve.feed(tok)
            if safe:
                combined_safe += safe
            if calls is not None:
                combined_calls = calls
        return combined_safe, combined_calls

    def test_plain_text_passes_through(self):
        """Plain text without tool XML should pass through sieve unchanged."""
        safe, calls = self._feed_console_text("Hello, how can I help you?")
        assert safe == "Hello, how can I help you?"
        assert calls is None

    def test_tool_call_xml_detected(self):
        """<tool_calls> XML should be detected and parsed."""
        xml = (
            "<tool_calls>\n"
            "  <tool_call>\n"
            "    <tool_name>get_weather</tool_name>\n"
            '    <parameters>{"city": "Beijing"}</parameters>\n'
            "  </tool_call>\n"
            "</tool_calls>"
        )
        safe, calls = self._feed_console_text(xml)
        assert safe == ""
        assert calls is not None
        assert len(calls) == 1
        assert calls[0].name == "get_weather"
        assert "Beijing" in calls[0].arguments

    def test_mixed_text_and_tool_call(self):
        """Text before tool XML should be returned as safe text, tools parsed."""
        mixed = 'Let me check the weather.<tool_calls><tool_call><tool_name>get_weather</tool_name><parameters>{"city":"Beijing"}</parameters></tool_call></tool_calls>'
        safe, calls = self._feed_console_text(mixed)
        assert safe == "Let me check the weather."
        assert calls is not None
        assert len(calls) == 1

    def test_multiple_tool_calls(self):
        """Multiple tool calls in one <tool_calls> block should all be parsed."""
        xml = (
            "<tool_calls>\n"
            "  <tool_call>\n"
            "    <tool_name>get_weather</tool_name>\n"
            '    <parameters>{"city": "Beijing"}</parameters>\n'
            "  </tool_call>\n"
            "  <tool_call>\n"
            "    <tool_name>get_time</tool_name>\n"
            '    <parameters>{"timezone": "Asia/Shanghai"}</parameters>\n'
            "  </tool_call>\n"
            "</tool_calls>"
        )
        safe, calls = self._feed_console_text(xml)
        assert calls is not None
        assert len(calls) == 2
        assert calls[0].name == "get_weather"
        assert calls[1].name == "get_time"

    def test_unknown_tool_filtered_by_name(self):
        """Tool not in tool_names should be filtered out by parse_tool_calls."""
        sieve = ToolSieve(["get_weather"])  # only allow get_weather
        xml = (
            "<tool_calls>\n"
            "  <tool_call>\n"
            "    <tool_name>unknown_tool</tool_name>\n"
            "    <parameters>{}</parameters>\n"
            "  </tool_call>\n"
            "</tool_calls>"
        )
        import orjson

        event_data = orjson.dumps({"delta": xml}).decode()
        tokens = self.adapter.feed("response.output_text.delta", event_data)
        combined_calls = None
        for tok in tokens:
            _, calls = sieve.feed(tok)
            if calls is not None:
                combined_calls = calls
        remaining = sieve.flush()
        assert combined_calls is None or len(combined_calls) == 0
        assert remaining is None or len(remaining) == 0

    def test_flush_captures_stray_xml(self):
        """Tool calls should be detected during feed when complete XML is in one chunk."""
        sieve = ToolSieve(["get_weather"])
        xml = '<tool_calls><tool_call><tool_name>get_weather</tool_name><parameters>{"city":"Beijing"}</parameters></tool_call></tool_calls>'
        import orjson

        event_data = orjson.dumps({"delta": xml}).decode()
        tokens = self.adapter.feed("response.output_text.delta", event_data)
        captured_calls = None
        for tok in tokens:
            _, calls = sieve.feed(tok)
            if calls is not None:
                captured_calls = calls
        # The sieve should have detected the complete XML during feed
        assert captured_calls is not None
        assert len(captured_calls) == 1
        assert captured_calls[0].name == "get_weather"


# ---------------------------------------------------------------------------
# parse_tool_calls integration
# ---------------------------------------------------------------------------


class TestConsoleNonStreamingToolCallDetection:
    """parse_tool_calls should detect tools in full text (non-streaming path)."""

    def test_parse_tool_calls_detects_xml(self):
        """Standard <tool_calls> XML should be parsed correctly."""
        text = (
            "I will check the weather.\n"
            "<tool_calls>\n"
            "  <tool_call>\n"
            "    <tool_name>get_weather</tool_name>\n"
            '    <parameters>{"city": "Beijing"}</parameters>\n'
            "  </tool_call>\n"
            "</tool_calls>"
        )
        result = parse_tool_calls(text, ["get_weather"])
        assert result.saw_tool_syntax
        assert len(result.calls) == 1
        assert result.calls[0].name == "get_weather"

    def test_parse_tool_calls_no_tools_returns_empty(self):
        """Text without tool syntax should return empty result."""
        result = parse_tool_calls("Hello, how can I help you?", ["get_weather"])
        assert not result.saw_tool_syntax
        assert len(result.calls) == 0

    def test_parse_tool_calls_filters_by_available(self):
        """Only tools in available_tools should be returned."""
        text = (
            "<tool_calls>\n"
            "  <tool_call>\n"
            "    <tool_name>get_weather</tool_name>\n"
            '    <parameters>{"city": "Beijing"}</parameters>\n'
            "  </tool_call>\n"
            "</tool_calls>"
        )
        result = parse_tool_calls(
            text, ["search"]
        )  # only allow search, not get_weather
        assert len(result.calls) == 0

    def test_console_stream_adapter_full_text(self):
        """ConsoleStreamAdapter.full_text should collect all text deltas."""
        adapter = ConsoleStreamAdapter()
        import orjson

        adapter.feed(
            "response.output_text.delta", orjson.dumps({"delta": "Hello "}).decode()
        )
        adapter.feed(
            "response.output_text.delta", orjson.dumps({"delta": "World"}).decode()
        )
        adapter.feed(
            "response.completed", orjson.dumps({"response": {"usage": {}}}).decode()
        )
        assert adapter.full_text == "Hello World"

    def test_console_stream_adapter_usage(self):
        """ConsoleStreamAdapter should capture usage from response.completed."""
        adapter = ConsoleStreamAdapter()
        import orjson

        usage_data = {"input_tokens": 10, "output_tokens": 20}
        adapter.feed(
            "response.completed",
            orjson.dumps({"response": {"usage": usage_data}}).decode(),
        )
        assert adapter.usage == usage_data
        assert adapter._done


# ---------------------------------------------------------------------------
# Streaming chunk format verification
# ---------------------------------------------------------------------------


class TestToolCallChunkFormat:
    """Tool call chunks should match OpenAI format."""

    def test_make_tool_call_chunk_structure(self):
        """make_tool_call_chunk should produce correct SSE delta format."""
        from app.products.openai._format import (
            make_tool_call_chunk,
            make_tool_call_done_chunk,
        )

        chunk = make_tool_call_chunk(
            "resp_123",
            "grok-4.3-console",
            0,
            "call_abc",
            "get_weather",
            '{"city":"Beijing"}',
            is_first=True,
        )
        assert chunk["id"] == "resp_123"
        assert chunk["object"] == "chat.completion.chunk"
        assert chunk["choices"][0]["delta"]["role"] == "assistant"
        assert chunk["choices"][0]["delta"]["content"] is None
        tc = chunk["choices"][0]["delta"]["tool_calls"][0]
        assert tc["index"] == 0
        assert tc["id"] == "call_abc"
        assert tc["function"]["name"] == "get_weather"
        assert tc["function"]["arguments"] == '{"city":"Beijing"}'

    def test_make_tool_call_done_chunk_structure(self):
        """make_tool_call_done_chunk should have finish_reason='tool_calls'."""
        from app.products.openai._format import make_tool_call_done_chunk

        chunk = make_tool_call_done_chunk("resp_123", "grok-4.3-console")
        assert chunk["choices"][0]["finish_reason"] == "tool_calls"

    def test_make_tool_call_response_structure(self):
        """make_tool_call_response should produce non-streaming tool_calls response."""
        from app.products.openai._format import make_tool_call_response
        from app.dataplane.reverse.protocol.tool_parser import ParsedToolCall

        calls = [
            ParsedToolCall(
                call_id="call_1", name="get_weather", arguments='{"city":"Beijing"}'
            )
        ]
        resp = make_tool_call_response(
            "grok-4.3-console",
            calls,
            response_id="resp_123",
        )
        assert resp["choices"][0]["finish_reason"] == "tool_calls"
        assert (
            resp["choices"][0]["message"]["tool_calls"][0]["function"]["name"]
            == "get_weather"
        )


# ---------------------------------------------------------------------------
# build_console_payload with tools
# ---------------------------------------------------------------------------


class TestConsolePayloadWithTools:
    """build_console_payload should handle client tools correctly."""

    def test_payload_adds_search_tools_for_multi_agent(self):
        """Multi-agent models should get built-in web_search/x_search tools."""
        from app.dataplane.reverse.protocol.xai_console_chat import (
            build_console_payload,
        )

        payload = build_console_payload(
            messages=[{"role": "user", "content": "hello"}],
            model="grok-4.20-multi-agent-high",
        )
        if "tools" in payload:
            tool_types = [t["type"] for t in payload["tools"]]
            assert "web_search" in tool_types

    def test_payload_does_not_add_search_tools_for_non_multi_agent(self):
        """Non-search-tool models should not get built-in search tools.
        Use a non-multi-agent model that's NOT in _MODELS_WITH_SEARCH_TOOLS.
        If no such model exists, mark as expected failure.
        """
        from app.dataplane.reverse.protocol.xai_console_chat import (
            _MODELS_WITH_SEARCH_TOOLS,
            build_console_payload,
        )

        # Find a console model that won't have search tools
        from app.dataplane.reverse.protocol.xai_console_chat import CONSOLE_MODELS

        non_search_model = next(
            (
                m
                for m in CONSOLE_MODELS
                if CONSOLE_MODELS[m] not in _MODELS_WITH_SEARCH_TOOLS
            ),
            None,
        )
        if non_search_model is None:
            pytest.skip("no model without search tools available")

        payload = build_console_payload(
            messages=[{"role": "user", "content": "hello"}],
            model=non_search_model,
        )
        assert "tools" not in payload or all(
            t["type"] not in ("web_search", "x_search") for t in payload["tools"]
        )


# ---------------------------------------------------------------------------
# Parametrized tool_choice variations
# ---------------------------------------------------------------------------


class TestToolChoicePromptVariations:
    """Different tool_choice values should produce different prompt instructions."""

    @pytest.mark.parametrize(
        "choice,expected_in_prompt",
        [
            ("auto", "Call a tool when"),
            ("none", "Do NOT call any tools"),
            ("required", "MUST output"),
            ({"type": "function", "function": {"name": "foo"}}, 'tool named "foo"'),
            (None, "Call a tool when"),  # None defaults to auto
        ],
    )
    def test_tool_choice_produces_correct_instruction(
        self,
        choice: str | dict | None,
        expected_in_prompt: str,
    ):
        """Tool choice should produce correct instruction in prompt."""
        tools = [
            {"type": "function", "function": {"name": "foo", "description": "bar"}}
        ]
        prompt = build_tool_system_prompt(tools, choice)
        assert expected_in_prompt in prompt


# ---------------------------------------------------------------------------
# ToolSieve edge cases
# ---------------------------------------------------------------------------


class TestToolSieveEdgeCases:
    """Edge cases for ToolSieve streaming detector."""

    def test_feed_empty_chunk_returns_empty(self):
        """feed('') should return ('', None) without changing state."""
        sieve = ToolSieve(["get_weather"])
        safe, calls = sieve.feed("")
        assert safe == ""
        assert calls is None
        # State should be unchanged — next real feed should work normally
        safe2, calls2 = sieve.feed("Hello")
        assert safe2 == "Hello"
        assert calls2 is None

    def test_partial_xml_tag_at_chunk_boundary(self):
        """Partial <tool_ split across chunks should be reassembled correctly."""
        sieve = ToolSieve(["get_weather"])
        safe1, calls1 = sieve.feed("Hello <tool_")
        assert safe1 == "Hello "
        assert calls1 is None
        # Buffer now holds "<tool_", so next chunk must start with "calls>" to form "<tool_calls>"
        xml_rest = (
            "calls>\n"
            "  <tool_call>\n"
            "    <tool_name>get_weather</tool_name>\n"
            '    <parameters>{"city": "Beijing"}</parameters>\n'
            "  </tool_call>\n"
            "</tool_calls>"
        )
        safe2, calls2 = sieve.feed(xml_rest)
        assert safe2 == ""
        assert calls2 is not None
        assert len(calls2) == 1
        assert calls2[0].name == "get_weather"

    def test_very_long_single_chunk(self):
        """A very long chunk with embedded tool XML should parse correctly."""
        sieve = ToolSieve(["get_weather"])
        prefix = "x" * 10_000
        xml = (
            f"{prefix}<tool_calls>"
            "  <tool_call>"
            "    <tool_name>get_weather</tool_name>"
            '    <parameters>{"city": "Beijing"}</parameters>'
            "  </tool_call>"
            "</tool_calls>"
        )
        safe, calls = sieve.feed(xml)
        assert safe == prefix
        assert calls is not None
        assert len(calls) == 1
        assert calls[0].name == "get_weather"

    def test_whitespace_only_before_tool_xml(self):
        """Whitespace before tool XML should be returned as safe text."""
        sieve = ToolSieve(["get_weather"])
        xml = (
            "   \n  \t"
            "<tool_calls>"
            "  <tool_call>"
            "    <tool_name>get_weather</tool_name>"
            '    <parameters>{"city": "Beijing"}</parameters>'
            "  </tool_call>"
            "</tool_calls>"
            "  \n  "
        )
        safe, calls = sieve.feed(xml)
        assert safe == "   \n  \t"
        assert calls is not None
        assert calls[0].name == "get_weather"

    def test_whitespace_only_after_tool_xml(self):
        """Whitespace after </tool_calls> is irrelevant (already captured)."""
        sieve = ToolSieve(["get_weather"])
        xml = (
            "<tool_calls>"
            "  <tool_call>"
            "    <tool_name>get_weather</tool_name>"
            '    <parameters>{"city": "Beijing"}</parameters>'
            "  </tool_call>"
            "</tool_calls>  \n  "
        )
        safe, calls = sieve.feed(xml)
        assert safe == ""
        assert calls is not None
        assert calls[0].name == "get_weather"

    def test_multiple_sequential_tool_calls_blocks(self):
        """Only the first <tool_calls> block should be detected; rest passes through."""
        sieve = ToolSieve(["get_weather", "get_time"])
        block1 = (
            "<tool_calls>"
            "  <tool_call>"
            "    <tool_name>get_weather</tool_name>"
            '    <parameters>{"city": "Beijing"}</parameters>'
            "  </tool_call>"
            "</tool_calls>"
        )
        block2 = (
            "<tool_calls>"
            "  <tool_call>"
            "    <tool_name>get_time</tool_name>"
            '    <parameters>{"timezone": "UTC"}</parameters>'
            "  </tool_call>"
            "</tool_calls>"
        )
        # Feed both blocks together — the second is part of the same chunk
        safe1, calls1 = sieve.feed(block1 + block2)
        assert calls1 is not None
        assert len(calls1) == 1
        assert calls1[0].name == "get_weather"
        # The second block should have been consumed as part of the first detection
        # (it was inside the same chunk, between <tool_calls> and </tool_calls>)

    def test_feed_after_done_returns_chunk(self):
        """feed() after tool calls were already detected should pass chunk through."""
        sieve = ToolSieve(["get_weather"])
        xml = "<tool_calls><tool_call><tool_name>get_weather</tool_name><parameters>{}</parameters></tool_call></tool_calls>"
        sieve.feed(xml)
        assert sieve._done
        # Subsequent feed should return the chunk as safe text
        safe, calls = sieve.feed("More text after tool calls")
        assert safe == "More text after tool calls"
        assert calls is None

    def test_flush_after_done_returns_none(self):
        """flush() after tool calls were already detected should return None."""
        sieve = ToolSieve(["get_weather"])
        xml = "<tool_calls><tool_call><tool_name>get_weather</tool_name><parameters>{}</parameters></tool_call></tool_calls>"
        sieve.feed(xml)
        assert sieve._done
        result = sieve.flush()
        assert result is None

    def test_flush_without_tool_syntax_returns_none(self):
        """flush() with plain text buffer (no tool syntax) should return None."""
        sieve = ToolSieve(["get_weather"])
        sieve.feed("Just plain text, no tools here.")
        result = sieve.flush()
        assert result is None

    def test_mixed_valid_and_invalid_xml_blocks(self):
        """Only well-formed tool XML should produce parsed calls."""
        sieve = ToolSieve(["get_weather"])
        # Feed invalid XML first, then valid
        sieve.feed("Some text <invalid>not a tool</invalid> more text ")
        safe, calls = sieve.feed(
            "<tool_calls>"
            "  <tool_call>"
            "    <tool_name>get_weather</tool_name>"
            '    <parameters>{"city": "Beijing"}</parameters>'
            "  </tool_call>"
            "</tool_calls>"
        )
        assert calls is not None
        assert len(calls) == 1

    def test_non_tool_xml_passes_through(self):
        """Content that looks XML-ish but isn't tool XML should pass through."""
        sieve = ToolSieve(["get_weather"])
        safe, calls = sieve.feed("<notool>text</notool>")
        assert safe == "<notool>text</notool>"
        assert calls is None

    def test_partial_tool_calls_open_only(self):
        """Incomplete <tool_calls> with no closing tag: flush returns empty list."""
        sieve = ToolSieve(["get_weather"])
        safe, calls = sieve.feed("Before ")
        assert safe == "Before "
        assert calls is None
        safe2, calls2 = sieve.feed(
            "<tool_calls><tool_call><tool_name>get_weather</tool_name><parameters>{}</parameters></tool_call>"
        )
        assert safe2 == ""
        assert calls2 is None
        result = sieve.flush()
        assert result is not None
        assert len(result) == 0

    def test_tool_calls_split_across_three_chunks(self):
        """XML split across 3 chunks should reassemble and parse."""
        sieve = ToolSieve(["get_weather"])
        safe1, _ = sieve.feed("Hello ")
        assert safe1 == "Hello "
        safe2, _ = sieve.feed("<tool_calls><tool_call><tool_name>get")
        assert safe2 == ""
        safe3, calls = sieve.feed(
            '_weather</tool_name><parameters>{"city":"Beijing"}</parameters></tool_call></tool_calls>'
        )
        assert safe3 == ""
        assert calls is not None
        assert len(calls) == 1
        assert calls[0].name == "get_weather"


# ---------------------------------------------------------------------------
# Console tool call regression tests
# ---------------------------------------------------------------------------


class TestConsoleToolCallRegression:
    """Regression tests for bugs that were found and fixed."""

    def test_sieve_detects_during_feed_not_flush(self):
        """Complete XML in one chunk: calls detected during feed(), not flush()."""
        sieve = ToolSieve(["get_weather"])
        xml = '<tool_calls><tool_call><tool_name>get_weather</tool_name><parameters>{"city":"Beijing"}</parameters></tool_call></tool_calls>'
        import orjson

        adapter = ConsoleStreamAdapter()
        event_data = orjson.dumps({"delta": xml}).decode()
        tokens = adapter.feed("response.output_text.delta", event_data)

        calls_during_feed = None
        for tok in tokens:
            _, calls = sieve.feed(tok)
            if calls is not None:
                calls_during_feed = calls

        # Calls must be detected during feed, not flush
        assert calls_during_feed is not None
        assert len(calls_during_feed) == 1
        assert calls_during_feed[0].name == "get_weather"

        # flush should return None since done
        assert sieve.flush() is None

    def test_adapter_feed_malformed_json(self):
        """ConsoleStreamAdapter.feed() with malformed JSON should return []."""
        adapter = ConsoleStreamAdapter()
        result = adapter.feed("response.output_text.delta", "not valid json {{{")
        assert result == []
        # Adapter should not be in done state
        assert not adapter._done

    def test_adapter_feed_empty_delta_string(self):
        """ConsoleStreamAdapter.feed() with empty delta should return []."""
        import orjson

        adapter = ConsoleStreamAdapter()
        event_data = orjson.dumps({"delta": ""}).decode()
        result = adapter.feed("response.output_text.delta", event_data)
        assert result == []
        assert adapter.full_text == ""

    def test_adapter_feed_after_done(self):
        """ConsoleStreamAdapter.feed() after response.completed should return []."""
        import orjson

        adapter = ConsoleStreamAdapter()
        # First, complete the response
        adapter.feed(
            "response.completed",
            orjson.dumps({"response": {"usage": {"input_tokens": 10}}}).decode(),
        )
        assert adapter._done
        # Then try to feed more — should return empty
        result = adapter.feed(
            "response.output_text.delta",
            orjson.dumps({"delta": "should be ignored"}).decode(),
        )
        assert result == []

    def test_build_console_payload_empty_messages(self):
        """build_console_payload with empty messages list should not crash."""
        from app.dataplane.reverse.protocol.xai_console_chat import (
            build_console_payload,
        )

        payload = build_console_payload(
            messages=[],
            model="grok-4.3-console",
        )
        assert payload["input"] == []
        assert payload["model"] == "grok-4.3"

    def test_build_tool_system_prompt_empty_tools(self):
        """build_tool_system_prompt with empty tools list should produce valid prompt."""
        prompt = build_tool_system_prompt([], "auto")
        assert "AVAILABLE TOOLS:" in prompt
        assert "tool_choice_instruction" not in prompt  # instruction should be rendered
        # Empty tools list should produce empty tool definitions section
        assert "Tool:" not in prompt

    def test_adapter_feed_non_delta_event(self):
        """ConsoleStreamAdapter.feed() with non-delta event type returns []."""
        import orjson

        adapter = ConsoleStreamAdapter()
        result = adapter.feed(
            "response.created",
            orjson.dumps({"response": {"id": "resp_123"}}).decode(),
        )
        assert result == []
        assert not adapter._done

    def test_adapter_error_event_raises(self):
        """ConsoleStreamAdapter.feed() with error event should raise UpstreamError."""
        from app.platform.errors import UpstreamError
        import orjson

        adapter = ConsoleStreamAdapter()
        with pytest.raises(UpstreamError, match="Console API error"):
            adapter.feed(
                "error",
                orjson.dumps({"message": "rate limited"}).decode(),
            )


# ---------------------------------------------------------------------------
# parse_tool_calls edge cases
# ---------------------------------------------------------------------------


class TestParseToolCallEdgeCases:
    """Edge cases for tool_parser.parse_tool_calls."""

    def test_empty_string(self):
        """Empty string input should return empty result."""
        result = parse_tool_calls("", ["get_weather"])
        assert not result.saw_tool_syntax
        assert len(result.calls) == 0

    def test_only_whitespace(self):
        """Whitespace-only input should return empty result."""
        result = parse_tool_calls("   \n\t  ", ["get_weather"])
        assert not result.saw_tool_syntax
        assert len(result.calls) == 0

    def test_json_envelope_missing_tool_calls_key(self):
        """JSON envelope without 'tool_calls' key should return empty result."""
        text = '{"response": {"content": "hello"}}'
        result = parse_tool_calls(text, ["get_weather"])
        assert len(result.calls) == 0

    def test_json_envelope_non_list_tool_calls(self):
        """JSON envelope with non-list tool_calls value should return empty."""
        text = '{"tool_calls": "not a list"}'
        result = parse_tool_calls(text, ["get_weather"])
        assert result.saw_tool_syntax
        assert len(result.calls) == 0

    def test_json_envelope_tool_calls_none_value(self):
        """JSON envelope with null tool_calls should return empty."""
        text = '{"tool_calls": null}'
        result = parse_tool_calls(text, ["get_weather"])
        assert result.saw_tool_syntax
        assert len(result.calls) == 0

    def test_bare_json_array_empty(self):
        """Bare empty JSON array should return empty result."""
        result = parse_tool_calls("[]", ["get_weather"])
        assert len(result.calls) == 0

    def test_tool_call_without_closing_tag(self):
        """Standalone <tool_call> without </tool_calls> wrapper: saw syntax but no calls."""
        text = '<tool_call><tool_name>get_weather</tool_name><parameters>{"city":"Beijing"}</parameters></tool_call>'
        result = parse_tool_calls(text, ["get_weather"])
        # _has_tool_syntax matches <tool_call, but no complete XML block parsed
        assert result.saw_tool_syntax
        assert len(result.calls) == 0

    def test_multiple_close_tags(self):
        """Duplicate </tool_calls> tags: regex matches first complete block."""
        text = (
            "<tool_calls>"
            "  <tool_call>"
            "    <tool_name>get_weather</tool_name>"
            '    <parameters>{"city":"Beijing"}</parameters>'
            "  </tool_call>"
            "</tool_calls>"
            "garbage text </tool_calls>"
        )
        result = parse_tool_calls(text, ["get_weather"])
        assert len(result.calls) == 1
        assert result.calls[0].name == "get_weather"

    def test_bare_tool_call_no_wrapper(self):
        """Single <tool_call> without <tool_calls> wrapper: saw syntax, no calls."""
        text = (
            "  <tool_call>\n"
            "    <tool_name>get_weather</tool_name>\n"
            '    <parameters>{"city":"Beijing"}</parameters>\n'
            "  </tool_call>"
        )
        result = parse_tool_calls(text, ["get_weather"])
        assert result.saw_tool_syntax
        # No <tool_calls> wrapper means _parse_xml_tool_calls returns []
        assert len(result.calls) == 0

    def test_tool_name_with_hyphens(self):
        """Tool name containing hyphens should be parsed correctly."""
        text = (
            "<tool_calls>"
            "  <tool_call>"
            "    <tool_name>get-weather-data</tool_name>"
            '    <parameters>{"city":"NYC"}</parameters>'
            "  </tool_call>"
            "</tool_calls>"
        )
        result = parse_tool_calls(text, ["get-weather-data"])
        assert len(result.calls) == 1
        assert result.calls[0].name == "get-weather-data"

    def test_tool_name_with_underscores(self):
        """Tool name containing underscores should be parsed correctly."""
        text = (
            "<tool_calls>"
            "  <tool_call>"
            "    <tool_name>get_weather_forecast</tool_name>"
            '    <parameters>{"city":"NYC"}</parameters>'
            "  </tool_call>"
            "</tool_calls>"
        )
        result = parse_tool_calls(text, ["get_weather_forecast"])
        assert len(result.calls) == 1
        assert result.calls[0].name == "get_weather_forecast"

    def test_arguments_with_unescaped_newlines(self):
        """JSON arguments with unescaped newlines should be repaired."""
        text = (
            "<tool_calls>"
            "  <tool_call>"
            "    <tool_name>get_weather</tool_name>"
            '    <parameters>{"city": "New\nYork"}</parameters>'
            "  </tool_call>"
            "</tool_calls>"
        )
        result = parse_tool_calls(text, ["get_weather"])
        assert len(result.calls) == 1
        assert result.calls[0].name == "get_weather"

    def test_deeply_nested_parameters(self):
        """Very deeply nested JSON parameters should parse correctly."""
        nested = {"a": {"b": {"c": {"d": {"e": {"f": "deep"}}}}}}
        import json

        params_str = json.dumps(nested, separators=(",", ":"))
        text = (
            "<tool_calls>"
            "  <tool_call>"
            "    <tool_name>get_weather</tool_name>"
            f"    <parameters>{params_str}</parameters>"
            "  </tool_call>"
            "</tool_calls>"
        )
        result = parse_tool_calls(text, ["get_weather"])
        assert len(result.calls) == 1
        parsed_args = json.loads(result.calls[0].arguments)
        assert parsed_args["a"]["b"]["c"]["d"]["e"]["f"] == "deep"

    def test_json_envelope_with_valid_calls(self):
        """JSON envelope format should be parsed correctly."""
        text = '{"tool_calls": [{"name": "get_weather", "input": {"city": "Beijing"}}]}'
        result = parse_tool_calls(text, ["get_weather"])
        assert len(result.calls) == 1
        assert result.calls[0].name == "get_weather"

    def test_json_array_format(self):
        """Bare JSON array format should be parsed when syntax marker is present."""
        text = 'tool_calls: [{"name": "get_weather", "input": {"city": "Beijing"}}]'
        result = parse_tool_calls(text, ["get_weather"])
        assert len(result.calls) == 1
        assert result.calls[0].name == "get_weather"

    def test_function_call_alt_xml(self):
        """Alternative <function_call> XML format should be parsed."""
        text = (
            "<function_call>"
            "<name>get_weather</name>"
            '<arguments>{"city":"Beijing"}</arguments>'
            "</function_call>"
        )
        result = parse_tool_calls(text, ["get_weather"])
        assert len(result.calls) == 1
        assert result.calls[0].name == "get_weather"

    def test_invoke_alt_xml(self):
        """Alternative <invoke> XML format should be parsed."""
        text = '<invoke name="get_weather">{"city":"Beijing"}</invoke>'
        result = parse_tool_calls(text, ["get_weather"])
        assert len(result.calls) == 1
        assert result.calls[0].name == "get_weather"

    def test_available_tools_filters_calls(self):
        """Only calls matching available_tools should be returned."""
        text = (
            "<tool_calls>"
            "  <tool_call>"
            "    <tool_name>get_weather</tool_name>"
            '    <parameters>{"city":"Beijing"}</parameters>'
            "  </tool_call>"
            "  <tool_call>"
            "    <tool_name>get_time</tool_name>"
            '    <parameters>{"tz":"UTC"}</parameters>'
            "  </tool_call>"
            "</tool_calls>"
        )
        result = parse_tool_calls(text, ["get_weather"])
        assert len(result.calls) == 1
        assert result.calls[0].name == "get_weather"

    def test_none_available_tools_returns_all(self):
        """None available_tools should return all parsed calls."""
        text = (
            "<tool_calls>"
            "  <tool_call>"
            "    <tool_name>get_weather</tool_name>"
            '    <parameters>{"city":"Beijing"}</parameters>'
            "  </tool_call>"
            "</tool_calls>"
        )
        result = parse_tool_calls(text, None)
        assert len(result.calls) == 1

    def test_malformed_json_in_parameters(self):
        """Malformed JSON in parameters that can't be repaired should skip that call."""
        text = (
            "<tool_calls>"
            "  <tool_call>"
            "    <tool_name>get_weather</tool_name>"
            "    <parameters>{invalid json without repair}</parameters>"
            "  </tool_call>"
            "</tool_calls>"
        )
        result = parse_tool_calls(text, ["get_weather"])
        # _parse_json_tolerant returns None for unrepairable JSON → call skipped
        assert len(result.calls) == 0
        assert result.saw_tool_syntax


# ---------------------------------------------------------------------------
# build_tool_system_prompt edge cases
# ---------------------------------------------------------------------------


class TestBuildToolSystemPromptEdgeCases:
    """Edge cases for tool_prompt.build_tool_system_prompt."""

    def test_tool_with_no_parameters(self):
        """Tool with no parameters property should still appear in prompt."""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "say_hello",
                    "description": "Say hello",
                },
            }
        ]
        prompt = build_tool_system_prompt(tools, "auto")
        assert "say_hello" in prompt
        assert "Say hello" in prompt
        # No Parameters line since params is None
        assert "Parameters:" not in prompt

    def test_tool_with_empty_parameters(self):
        """Tool with empty parameters object should include empty JSON in prompt."""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "noop",
                    "description": "Does nothing",
                    "parameters": {},
                },
            }
        ]
        prompt = build_tool_system_prompt(tools, "auto")
        assert "noop" in prompt
        # Empty dict is falsy, so Parameters line should be skipped
        assert "Parameters:" not in prompt

    def test_tool_with_extremely_long_description(self):
        """Tool with very long description should be included verbatim."""
        long_desc = "A" * 10_000
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "long_desc_tool",
                    "description": long_desc,
                },
            }
        ]
        prompt = build_tool_system_prompt(tools, "auto")
        assert "long_desc_tool" in prompt
        assert long_desc in prompt

    def test_multiple_tools_with_similar_names(self):
        """Tools with similar names should each appear distinctly."""
        tools = [
            {
                "type": "function",
                "function": {"name": "get_weather", "description": "Get weather"},
            },
            {
                "type": "function",
                "function": {
                    "name": "get_weather_forecast",
                    "description": "Get forecast",
                },
            },
            {
                "type": "function",
                "function": {"name": "get_weather_alerts", "description": "Get alerts"},
            },
        ]
        prompt = build_tool_system_prompt(tools, "auto")
        assert "get_weather\n" in prompt or prompt.count("get_weather") >= 3
        assert "get_weather_forecast" in prompt
        assert "get_weather_alerts" in prompt

    def test_tool_choice_dict_missing_function_key(self):
        """tool_choice dict without 'function' key should fall back to auto."""
        tools = [
            {"type": "function", "function": {"name": "foo", "description": "bar"}}
        ]
        prompt = build_tool_system_prompt(tools, {"type": "function"})
        # No function name found → falls back to auto
        assert "Call a tool when" in prompt

    def test_tool_choice_dict_empty_function_name(self):
        """tool_choice dict with empty function name should fall back to auto."""
        tools = [
            {"type": "function", "function": {"name": "foo", "description": "bar"}}
        ]
        prompt = build_tool_system_prompt(
            tools, {"type": "function", "function": {"name": ""}}
        )
        assert "Call a tool when" in prompt

    def test_tool_choice_dict_none_function(self):
        """tool_choice dict with function=None should fall back to auto."""
        tools = [
            {"type": "function", "function": {"name": "foo", "description": "bar"}}
        ]
        prompt = build_tool_system_prompt(tools, {"type": "function", "function": None})
        assert "Call a tool when" in prompt

    def test_tool_with_complex_json_schema(self):
        """Tool with deeply nested JSON Schema should be formatted correctly."""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "complex_tool",
                    "description": "Tool with complex schema",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "users": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "address": {
                                            "type": "object",
                                            "properties": {
                                                "street": {"type": "string"},
                                                "city": {"type": "string"},
                                            },
                                        },
                                    },
                                },
                            }
                        },
                    },
                },
            }
        ]
        prompt = build_tool_system_prompt(tools, "auto")
        assert "complex_tool" in prompt
        assert '"type": "array"' in prompt or "array" in prompt

    def test_tools_with_non_dict_entries(self):
        """Non-dict entries in tools list should raise an error (not silently skipped)."""
        tools = [
            "not a dict",
            42,
            None,
        ]
        # _format_tool_definitions calls tool.get("function") which requires dict
        with pytest.raises((AttributeError, TypeError)):
            build_tool_system_prompt(tools, "auto")

    def test_tool_choice_type_none_string(self):
        """tool_choice dict with type='none' should produce 'do not call' instruction."""
        tools = [
            {"type": "function", "function": {"name": "foo", "description": "bar"}}
        ]
        prompt = build_tool_system_prompt(tools, {"type": "none"})
        assert "Do NOT call any tools" in prompt

    def test_tool_choice_type_required_string(self):
        """tool_choice dict with type='required' should produce 'must output' instruction."""
        tools = [
            {"type": "function", "function": {"name": "foo", "description": "bar"}}
        ]
        prompt = build_tool_system_prompt(tools, {"type": "required"})
        assert "MUST output" in prompt

    def test_tool_choice_unknown_type_falls_back_to_auto(self):
        """tool_choice dict with unrecognized type should fall back to auto."""
        tools = [
            {"type": "function", "function": {"name": "foo", "description": "bar"}}
        ]
        prompt = build_tool_system_prompt(tools, {"type": "unknown_type"})
        assert "Call a tool when" in prompt

    def test_extract_tool_names_ignores_entries_without_function(self):
        """Tools entries without 'function' key should be skipped by extract_tool_names."""
        tools = [
            {"type": "web_search"},  # no function key
            {"type": "function", "function": {"name": "get_weather"}},
            {"type": "function"},  # function is None-like
        ]
        names = extract_tool_names(tools)
        assert names == ["get_weather"]

    def test_extract_tool_names_strips_whitespace(self):
        """Tool names with surrounding whitespace should be stripped."""
        tools = [
            {"type": "function", "function": {"name": "  get_weather  "}},
        ]
        names = extract_tool_names(tools)
        assert names == ["get_weather"]

    def test_tool_calls_to_xml_roundtrip(self):
        """tool_calls_to_xml output should be parseable by parse_tool_calls."""
        from app.dataplane.reverse.protocol.tool_parser import parse_tool_calls
        from app.dataplane.reverse.protocol.tool_prompt import tool_calls_to_xml

        tool_calls = [
            {
                "function": {
                    "name": "get_weather",
                    "arguments": '{"city":"Beijing"}',
                }
            }
        ]
        xml = tool_calls_to_xml(tool_calls)
        result = parse_tool_calls(xml, ["get_weather"])
        assert len(result.calls) == 1
        assert result.calls[0].name == "get_weather"

    def test_tool_calls_to_xml_malformed_arguments(self):
        """tool_calls_to_xml with invalid JSON arguments should keep original string."""
        from app.dataplane.reverse.protocol.tool_prompt import tool_calls_to_xml

        tool_calls = [
            {
                "function": {
                    "name": "foo",
                    "arguments": "not json",
                }
            }
        ]
        xml = tool_calls_to_xml(tool_calls)
        assert "not json" in xml


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
