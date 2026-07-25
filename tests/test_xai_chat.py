"""StreamAdapter unit tests — _finished flag, feed() error semantics,
URL sanitization, title sanitization, search result capping, web search counter."""

import json
import pytest
from app.dataplane.reverse.protocol.xai_chat import (
    StreamAdapter,
    FrameEvent,
    _sanitize_url,
    _sanitize_title,
    MAX_WEB_SEARCH_RESULTS,
)
from app.platform.errors import UpstreamError


class TestStreamAdapterFinished:
    """Test StreamAdapter._finished flag behavior.

    The _finished flag is set to True when feed() receives an error dict,
    ensuring subsequent feed() calls return [] immediately.  This prevents
    downstream processing of frames that arrive after an upstream error.
    """

    def test_normal_data_does_not_set_finished(self):
        """Given a normal payload, _finished remains False after feed()."""
        adapter = StreamAdapter()
        adapter.feed(
            '{"result": {"response": {"token": "hello", "messageTag": "final"}}}'
        )
        assert adapter._finished is False

    def test_finished_set_on_error_dict(self):
        """Given an error dict payload, _finished is set True before raising."""
        adapter = StreamAdapter()
        with pytest.raises(UpstreamError):
            adapter.feed('{"error": {"message": "rate limit", "code": 8}}')
        assert adapter._finished is True

    def test_finished_set_before_raise(self):
        """_finished is set True BEFORE raise_for_stream_error — verify via flag order."""
        adapter = StreamAdapter()
        try:
            adapter.feed('{"error": {"message": "upstream error"}}')
        except UpstreamError:
            pass
        assert adapter._finished is True

    def test_subsequent_calls_return_empty(self):
        """After an error sets _finished, future feed() calls return []."""
        adapter = StreamAdapter()
        try:
            adapter.feed('{"error": {"message": "upstream error"}}')
        except UpstreamError:
            pass

        assert adapter._finished is True
        events = adapter.feed(
            '{"result": {"response": {"token": "hello", "messageTag": "final"}}}'
        )
        assert events == []

    def test_string_error_does_not_set_finished(self):
        """String error (not dict) — _finished stays False, no exception."""
        adapter = StreamAdapter()
        events = adapter.feed('{"error": "upstream error message"}')
        assert adapter._finished is False
        assert events == []  # no result, so no events

    def test_error_without_result_returns_empty(self):
        """Error dict with no result → _finished True, no events."""
        adapter = StreamAdapter()
        try:
            adapter.feed('{"error": {"message": "something broke"}}')
        except UpstreamError:
            pass
        assert adapter._finished is True

    def test_soft_stop_does_not_set_finished(self):
        """Soft stop is not an error — _finished stays False."""
        adapter = StreamAdapter()
        events = adapter.feed('{"result": {"response": {"isSoftStop": true}}}')
        assert adapter._finished is False
        assert len(events) == 1
        assert events[0].kind == "soft_stop"

    def test_normal_payload_without_error(self):
        """Happy path — no error, _finished stays False, events returned."""
        adapter = StreamAdapter()
        events = adapter.feed(
            '{"result": {"response": {"token": "Hello", "isThinking": false, "messageTag": "final"}}}'
        )
        assert adapter._finished is False
        assert len(events) == 1
        assert events[0].kind == "text"
        assert events[0].content == "Hello"

    def test_empty_result_keeps_finished_false(self):
        """Empty result block (no response) — _finished stays False."""
        adapter = StreamAdapter()
        events = adapter.feed('{"result": {}}')
        assert adapter._finished is False
        assert events == []

    def test_none_error_not_confused_with_dict(self):
        """{'error': None} — not a dict, _finished stays False."""
        adapter = StreamAdapter()
        events = adapter.feed(
            '{"result": {"response": {"isSoftStop": true}}, "error": null}'
        )
        assert adapter._finished is False
        assert len(events) == 1
        assert events[0].kind == "soft_stop"

    def test_final_metadata_does_not_set_finished(self):
        """finalMetadata is not an error — _finished stays False."""
        adapter = StreamAdapter()
        events = adapter.feed('{"result": {"response": {"finalMetadata": true}}}')
        assert adapter._finished is False

    # ------------------------------------------------------------------
    # Edge cases — non-dict error, malformed input, sequential calls
    # ------------------------------------------------------------------

    def test_malformed_json_returns_empty_no_finished(self):
        """Malformed JSON → [] returned, _finished stays False."""
        adapter = StreamAdapter()
        events = adapter.feed("{not valid json}")
        assert events == []
        assert adapter._finished is False

    def test_empty_data_returns_empty_no_finished(self):
        """Empty string → [] returned, _finished stays False."""
        adapter = StreamAdapter()
        events = adapter.feed("")
        assert events == []
        assert adapter._finished is False

    def test_non_dict_error_list_not_finished(self):
        """error: [] (list, not dict) → _finished stays False, no exception."""
        adapter = StreamAdapter()
        events = adapter.feed('{"error": ["list", "of", "errors"]}')
        assert adapter._finished is False
        assert events == []  # no result, so no events

    def test_non_dict_error_number_not_finished(self):
        """error: 123 (number, not dict) → _finished stays False, no exception."""
        adapter = StreamAdapter()
        events = adapter.feed('{"error": 123}')
        assert adapter._finished is False
        assert events == []  # no result, so no events

    def test_sequential_feed_calls_independent(self):
        """Two successful feed() calls in sequence both return events."""
        adapter = StreamAdapter()
        e1 = adapter.feed(
            '{"result": {"response": {"token": "Hello", "isThinking": false, "messageTag": "final"}}}'
        )
        assert len(e1) == 1
        assert e1[0].kind == "text"
        assert adapter._finished is False

        e2 = adapter.feed(
            '{"result": {"response": {"token": " world", "isThinking": false, "messageTag": "final"}}}'
        )
        assert len(e2) == 1
        assert e2[0].kind == "text"
        assert adapter._finished is False

        assert adapter.text_buf == ["Hello", " world"]

    def test_error_then_valid_no_leak(self):
        """Error dict, then valid data (ignored because _finished)."""
        adapter = StreamAdapter()
        with pytest.raises(UpstreamError):
            adapter.feed('{"error": {"message": "boom"}}')
        assert adapter._finished is True

        events = adapter.feed(
            '{"result": {"response": {"token": "leak", "messageTag": "final"}}}'
        )
        assert events == []

    def test_empty_result_still_empty_response(self):
        """result: {} with no response key → [] returned."""
        adapter = StreamAdapter()
        events = adapter.feed('{"result": {}}')
        assert events == []
        assert adapter._finished is False

    def test_result_with_null_response(self):
        """result: {"response": null} → [] returned."""
        adapter = StreamAdapter()
        events = adapter.feed('{"result": {"response": null}}')
        assert events == []
        assert adapter._finished is False


# ------------------------------------------------------------------
# _sanitize_url tests
# ------------------------------------------------------------------


class TestSanitizeUrl:
    """Tests for URL sanitization in search results."""

    def test_valid_https_url(self):
        url, valid = _sanitize_url("https://example.com/path")
        assert valid is True
        assert url == "https://example.com/path"

    def test_valid_http_url(self):
        url, valid = _sanitize_url("http://example.com/path")
        assert valid is True
        assert url == "http://example.com/path"

    def test_trailing_slash_trimmed(self):
        url, valid = _sanitize_url("https://example.com/path/")
        assert valid is True
        assert url == "https://example.com/path"

    def test_multiple_trailing_slashes_trimmed(self):
        url, valid = _sanitize_url("https://example.com/path///")
        assert valid is True
        assert url == "https://example.com/path"

    def test_empty_string_rejected(self):
        url, valid = _sanitize_url("")
        assert valid is False
        assert url == ""

    def test_whitespace_only_rejected(self):
        url, valid = _sanitize_url("   ")
        assert valid is False
        assert url == ""

    def test_control_char_bel_rejected(self):
        """0x07 (BEL) in URL → rejected."""
        url, valid = _sanitize_url("https://exam\x07ple.com")
        assert valid is False

    def test_control_char_newline_rejected(self):
        url, valid = _sanitize_url("https://example\n.com/path")
        assert valid is False

    def test_control_char_tab_rejected(self):
        url, valid = _sanitize_url("https://example.com\tpath")
        assert valid is False

    def test_zero_width_space_rejected(self):
        """U+200B (zero-width space) → rejected."""
        url, valid = _sanitize_url("https://example\u200b.com")
        assert valid is False

    def test_bom_rejected(self):
        """U+FEFF (BOM) → rejected."""
        url, valid = _sanitize_url("https://example\ufeff.com")
        assert valid is False

    def test_non_http_protocol_rejected(self):
        url, valid = _sanitize_url("ftp://example.com")
        assert valid is False

    def test_no_protocol_rejected(self):
        url, valid = _sanitize_url("example.com/path")
        assert valid is False

    def test_very_long_url_rejected(self):
        """URL exceeding _MAX_URL_BYTES (2048) → rejected."""
        long_path = "a" * 2050
        url, valid = _sanitize_url(f"https://example.com/{long_path}")
        assert valid is False

    def test_url_at_max_length_accepted(self):
        """URL exactly at _MAX_URL_BYTES → accepted."""
        # 22 for "https://example.com/" + padding
        path_len = 2048 - len("https://example.com/")
        path = "a" * path_len
        url, valid = _sanitize_url(f"https://example.com/{path}")
        assert valid is True

    def test_url_with_query_params(self):
        url, valid = _sanitize_url("https://example.com/search?q=python&page=1")
        assert valid is True
        assert url == "https://example.com/search?q=python&page=1"

    def test_url_with_fragment(self):
        url, valid = _sanitize_url("https://example.com/page#section")
        assert valid is True
        assert url == "https://example.com/page#section"

    def test_url_with_port(self):
        url, valid = _sanitize_url("https://example.com:8080/path")
        assert valid is True

    def test_delete_char_rejected(self):
        """U+007F (DEL) → rejected."""
        url, valid = _sanitize_url("https://example\x7f.com")
        assert valid is False

    def test_url_with_unicode_accepted(self):
        url, valid = _sanitize_url("https://example.com/路径")
        assert valid is True


# ------------------------------------------------------------------
# _sanitize_title tests
# ------------------------------------------------------------------


class TestSanitizeTitle:
    """Tests for title sanitization in search results."""

    def test_normal_title_unchanged(self):
        title = _sanitize_title("A Normal Title", "https://example.com")
        assert title == "A Normal Title"

    def test_leading_trailing_whitespace_stripped(self):
        title = _sanitize_title("  Padded  ", "https://example.com")
        assert title == "Padded"

    def test_control_chars_stripped(self):
        title = _sanitize_title("Title\x00with\x07nulls", "https://example.com")
        assert title == "Titlewithnulls"

    def test_delete_char_stripped(self):
        title = _sanitize_title("Title\x7fdel", "https://example.com")
        assert title == "Titledel"

    def test_long_title_truncated(self):
        """Title exceeding _MAX_TITLE_RUNES (200) → truncated."""
        long_title = "A" * 300
        title = _sanitize_title(long_title, "https://example.com")
        assert len(title) == 200
        assert title == "A" * 200

    def test_title_at_max_length_not_truncated(self):
        title_200 = "B" * 200
        title = _sanitize_title(title_200, "https://example.com")
        assert title == title_200
        assert len(title) == 200

    def test_empty_title_falls_back_to_hostname(self):
        title = _sanitize_title("", "https://www.example.com/path")
        assert title == "www.example.com"

    def test_whitespace_only_title_falls_back(self):
        title = _sanitize_title("   ", "https://docs.python.org")
        assert title == "docs.python.org"

    def test_control_only_title_falls_back(self):
        title = _sanitize_title("\x00\x07\x1f", "https://example.com")
        assert title == "example.com"

    def test_unicode_title_preserved(self):
        title = _sanitize_title("日本語タイトル", "https://example.com")
        assert title == "日本語タイトル"

    def test_spaces_preserved(self):
        title = _sanitize_title("Multiple   Spaces   Here", "https://example.com")
        assert title == "Multiple   Spaces   Here"


# ------------------------------------------------------------------
# MAX_WEB_SEARCH_RESULTS capping
# ------------------------------------------------------------------


class TestSearchResultCapping:
    """Tests that web search results are capped at MAX_WEB_SEARCH_RESULTS."""

    @staticmethod
    def _make_search_frame(results: list[dict]) -> str:
        return json.dumps(
            {
                "result": {
                    "response": {
                        "webSearchResults": {"results": results},
                    }
                }
            }
        )

    def test_max_constant_is_50(self):
        assert MAX_WEB_SEARCH_RESULTS == 50

    def test_fewer_than_cap_all_kept(self):
        adapter = StreamAdapter()
        results = [
            {"url": f"https://example.com/{i}", "title": f"Page {i}"} for i in range(5)
        ]
        adapter.feed(self._make_search_frame(results))
        assert len(adapter._web_search_results) == 5

    def test_exactly_at_cap(self):
        adapter = StreamAdapter()
        results = [
            {"url": f"https://example.com/{i}", "title": f"Page {i}"} for i in range(50)
        ]
        adapter.feed(self._make_search_frame(results))
        assert len(adapter._web_search_results) == 50

    def test_over_cap_capped_to_50(self):
        adapter = StreamAdapter()
        results = [
            {"url": f"https://example.com/{i}", "title": f"Page {i}"}
            for i in range(100)
        ]
        adapter.feed(self._make_search_frame(results))
        assert len(adapter._web_search_results) == MAX_WEB_SEARCH_RESULTS

    def test_zero_results(self):
        adapter = StreamAdapter()
        adapter.feed(self._make_search_frame([]))
        assert len(adapter._web_search_results) == 0

    def test_across_multiple_frames(self):
        """Results accumulated across multiple feed() calls still capped."""
        adapter = StreamAdapter()
        r1 = [{"url": f"https://a.com/{i}", "title": f"A {i}"} for i in range(30)]
        r2 = [{"url": f"https://b.com/{i}", "title": f"B {i}"} for i in range(30)]
        adapter.feed(self._make_search_frame(r1))
        assert len(adapter._web_search_results) == 30
        adapter.feed(self._make_search_frame(r2))
        assert len(adapter._web_search_results) == 50

    def test_duplicate_urls_not_counted(self):
        """Duplicate URLs across frames → deduped, not double-counted."""
        adapter = StreamAdapter()
        results = [{"url": "https://same.com/x", "title": "Same"}]
        adapter.feed(self._make_search_frame(results))
        adapter.feed(self._make_search_frame(results))
        assert len(adapter._web_search_results) == 1

    def test_invalid_url_skipped(self):
        """URLs that fail sanitization are skipped."""
        adapter = StreamAdapter()
        results = [
            {"url": "https://good.com", "title": "Good"},
            {"url": "not-a-url", "title": "Bad"},
            {"url": "", "title": "Empty"},
        ]
        adapter.feed(self._make_search_frame(results))
        assert len(adapter._web_search_results) == 1
        assert adapter._web_search_results[0]["url"] == "https://good.com"


# ------------------------------------------------------------------
# _web_search_requests counter
# ------------------------------------------------------------------


class TestWebSearchRequestsCounter:
    """Tests that _web_search_requests counts distinct web search tool invocations."""

    def test_counter_starts_zero(self):
        adapter = StreamAdapter()
        assert adapter.web_search_requests_count() == 0

    def test_counter_increments_on_websearch_card(self):
        adapter = StreamAdapter()
        frame = json.dumps(
            {
                "result": {
                    "response": {
                        "messageTag": "tool_usage_card",
                        "toolUsageCard": {
                            "toolUsageCardId": "tc1",
                            "webSearch": {"query": "python"},
                        },
                    }
                }
            }
        )
        adapter.feed(frame)
        assert adapter.web_search_requests_count() == 1

    def test_counter_increments_per_invocation(self):
        adapter = StreamAdapter()
        frame = json.dumps(
            {
                "result": {
                    "response": {
                        "messageTag": "tool_usage_card",
                        "toolUsageCard": {
                            "toolUsageCardId": "tc1",
                            "webSearch": {"query": "test"},
                        },
                    }
                }
            }
        )
        adapter.feed(frame)
        adapter.feed(frame)
        assert adapter.web_search_requests_count() == 2

    def test_non_websearch_card_not_counted(self):
        adapter = StreamAdapter()
        frame = json.dumps(
            {
                "result": {
                    "response": {
                        "messageTag": "tool_usage_card",
                        "toolUsageCard": {
                            "toolUsageCardId": "tc1",
                            "codeExecution": {"code": "print(1)"},
                        },
                    }
                }
            }
        )
        adapter.feed(frame)
        assert adapter.web_search_requests_count() == 0

    def test_search_sources_list_returns_structured(self):
        """search_sources_list() returns list of dicts with url/title/type."""
        adapter = StreamAdapter()
        results = [
            {"url": "https://a.com", "title": "A", "type": "web"},
            {"url": "https://b.com", "title": "B", "type": "web"},
        ]
        adapter.feed(
            json.dumps(
                {"result": {"response": {"webSearchResults": {"results": results}}}}
            )
        )
        sources = adapter.search_sources_list()
        assert sources is not None
        assert len(sources) == 2
        assert sources[0]["url"] == "https://a.com"
        assert sources[0]["title"] == "A"
        assert sources[0]["type"] == "web"

    def test_search_sources_list_none_when_empty(self):
        adapter = StreamAdapter()
        assert adapter.search_sources_list() is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
