"""StreamAdapter unit tests — _finished flag, feed() error semantics."""

import pytest
from app.dataplane.reverse.protocol.xai_chat import StreamAdapter, FrameEvent
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
