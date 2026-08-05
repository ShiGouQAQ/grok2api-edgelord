"""Streamed function_call arguments integer normalization.

Port of Go 8b5c1ed6 cli/responses_response.go rewriteStreamData: streamed
function_call_arguments deltas are buffered per call, on .done the full
arguments are normalized via normalize_function_arguments (integral float
spellings like 60000.0 → 60000), and a corrected delta + done are re-emitted.
On buffer overflow the call switches to passthrough: buffered text is flushed
verbatim as one delta and later deltas pass through raw.
"""

import json

from app.dataplane.reverse.protocol.xai_build import BuildStreamAdapter
from app.products.openai.build_responses import _function_schemas

SCHEMA = {
    "type": "object",
    "properties": {
        "timeout_ms": {"type": "integer"},
        "ratio": {"type": "number"},
    },
}

TOOLS = [
    {
        "type": "function",
        "name": "wait_agent",
        "description": "wait",
        "parameters": SCHEMA,
    }
]


def _added(item_id: str = "fc_1", call_id: str = "call_1", name: str = "wait_agent"):
    return (
        "response.output_item.added",
        json.dumps(
            {
                "type": "response.output_item.added",
                "item": {
                    "id": item_id,
                    "type": "function_call",
                    "call_id": call_id,
                    "name": name,
                    "arguments": "",
                },
            }
        ),
    )


def _delta(item_id: str, delta: str):
    return (
        "response.function_call_arguments.delta",
        json.dumps(
            {
                "type": "response.function_call_arguments.delta",
                "item_id": item_id,
                "delta": delta,
            }
        ),
    )


def _done(item_id: str, arguments: str | None = None):
    payload = {
        "type": "response.function_call_arguments.done",
        "item_id": item_id,
    }
    if arguments is not None:
        payload["arguments"] = arguments
    return ("response.function_call_arguments.done", json.dumps(payload))


def _item_done(arguments: str):
    return (
        "response.output_item.done",
        json.dumps(
            {
                "type": "response.output_item.done",
                "item": {
                    "id": "fc_1",
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "wait_agent",
                    "arguments": arguments,
                },
            }
        ),
    )


def _sse_dicts(events):
    return [e["payload"] for e in events]


def test_stream_normalizes_float_integer_arguments():
    """60000.0 streamed via delta fragments reaches .done normalized to 60000,
    with a corrected delta + done re-emitted and the output_item.done item
    normalized too (Go 8b5c1ed6 TestResponsesIntegerArgumentsNormalizedInStream)."""
    adapter = BuildStreamAdapter(schemas={"wait_agent": SCHEMA})
    added = adapter.feed(*_added())
    assert len(added) == 1 and added[0]["type"] == "sse"
    assert added[0]["event"] == "response.output_item.added"

    # Deltas are buffered (suppressed) while accumulating.
    assert adapter.feed(*_delta("fc_1", '{"timeout_ms":60000')) == []
    assert adapter.feed(*_delta("fc_1", '.0,"ratio":2.0}')) == []

    done_events = adapter.feed(*_done("fc_1", '{"timeout_ms":60000.0,"ratio":2.0}'))
    assert len(done_events) == 2
    assert done_events[0]["event"] == "response.function_call_arguments.delta"
    assert done_events[1]["event"] == "response.function_call_arguments.done"
    payloads = _sse_dicts(done_events)
    assert json.loads(payloads[0]["delta"]) == {"timeout_ms": 60000, "ratio": 2.0}
    assert json.loads(payloads[1]["arguments"]) == {"timeout_ms": 60000, "ratio": 2.0}
    assert "60000.0" not in json.dumps(payloads)

    # The completed item in output_item.done is normalized as well.
    item_events = adapter.feed(*_item_done('{"timeout_ms":60000.0,"ratio":2.0}'))
    assert len(item_events) == 1
    item = item_events[0]["payload"]["item"]
    assert json.loads(item["arguments"]) == {"timeout_ms": 60000, "ratio": 2.0}


def test_stream_valid_integers_unchanged():
    adapter = BuildStreamAdapter(schemas={"wait_agent": SCHEMA})
    adapter.feed(*_added())
    assert adapter.feed(*_delta("fc_1", '{"timeout_ms":60000,"ratio":2.0}')) == []
    events = adapter.feed(*_done("fc_1", '{"timeout_ms":60000,"ratio":2.0}'))
    assert len(events) == 2
    args = json.loads(events[1]["payload"]["arguments"])
    assert args == {"timeout_ms": 60000, "ratio": 2.0}
    assert "60000.0" not in json.dumps(events)


def test_stream_overflow_passthrough_flushes_buffered_text():
    """Over-limit feed releases the buffer and emits buffered text as one
    delta + the current delta verbatim; later deltas pass through raw and
    .done still normalizes the final arguments (Go overflow semantics)."""
    from app.dataplane.reverse.protocol.tool_parser import (
        MAX_BUFFERED_FUNCTION_ARGUMENTS_BYTES,
    )

    adapter = BuildStreamAdapter(schemas={"wait_agent": SCHEMA})
    adapter.feed(*_added())
    big = "x" * MAX_BUFFERED_FUNCTION_ARGUMENTS_BYTES
    assert adapter.feed(*_delta("fc_1", big)) == []

    events = adapter.feed(*_delta("fc_1", '{"timeout_ms":60000.0}'))
    assert len(events) == 2
    assert events[0]["event"] == "response.function_call_arguments.delta"
    assert events[0]["payload"]["delta"] == big
    assert events[1]["event"] == "response.function_call_arguments.delta"
    assert events[1]["payload"]["delta"] == '{"timeout_ms":60000.0}'

    # Passthrough: subsequent deltas are emitted verbatim, one per event.
    events = adapter.feed(*_delta("fc_1", "tail"))
    assert len(events) == 1
    assert events[0]["payload"]["delta"] == "tail"

    # .done in passthrough mode: single done event, arguments normalized.
    events = adapter.feed(*_done("fc_1", '{"timeout_ms":60000.0}'))
    assert len(events) == 1
    assert events[0]["event"] == "response.function_call_arguments.done"
    assert json.loads(events[0]["payload"]["arguments"]) == {"timeout_ms": 60000}


def test_stream_done_without_deltas_still_normalizes():
    """done carrying full arguments with no prior deltas emits a single
    normalized done event (no corrected delta)."""
    adapter = BuildStreamAdapter(schemas={"wait_agent": SCHEMA})
    adapter.feed(*_added())
    events = adapter.feed(*_done("fc_1", '{"timeout_ms":6e4}'))
    assert len(events) == 1
    assert events[0]["event"] == "response.function_call_arguments.done"
    assert json.loads(events[0]["payload"]["arguments"]) == {"timeout_ms": 60000}


def test_stream_done_empty_arguments():
    """done with no arguments field and no buffered deltas passes through
    with empty arguments."""
    adapter = BuildStreamAdapter(schemas={"wait_agent": SCHEMA})
    adapter.feed(*_added())
    events = adapter.feed(*_done("fc_1"))
    assert len(events) == 1
    assert events[0]["payload"]["arguments"] == ""


def test_stream_unknown_call_dropped():
    """delta/done for an un-remembered call (no prior output_item.added) is
    dropped — internal tools never leak to the client."""
    adapter = BuildStreamAdapter(schemas={"wait_agent": SCHEMA})
    assert adapter.feed(*_delta("fc_9", '{"timeout_ms":60000.0}')) == []
    assert adapter.feed(*_done("fc_9", '{"timeout_ms":60000.0}')) == []


def test_stream_function_without_schema_passes_through():
    """A remembered function call whose schema is absent (no integer
    constraint) passes delta/done through verbatim."""
    adapter = BuildStreamAdapter(schemas={})
    adapter.feed(*_added())
    events = adapter.feed(*_delta("fc_1", '{"timeout_ms":60000.0}'))
    assert len(events) == 1
    assert events[0]["payload"]["delta"] == '{"timeout_ms":60000.0}'
    events = adapter.feed(*_done("fc_1", '{"timeout_ms":60000.0}'))
    assert len(events) == 1
    assert events[0]["payload"]["arguments"] == '{"timeout_ms":60000.0}'


def test_function_schemas_collects_integer_schemas():
    schemas = _function_schemas(TOOLS)
    assert schemas == {"wait_agent": SCHEMA}


def test_function_schemas_skips_non_integer_and_hosted():
    tools = [
        {
            "type": "function",
            "name": "plain",
            "parameters": {
                "type": "object",
                "properties": {"s": {"type": "string"}},
            },
        },
        {"type": "web_search"},
        {
            "type": "function",
            "name": "g",
            "parameters": {
                "type": "object",
                "properties": {"n": {"type": "integer"}},
            },
        },
    ]
    assert set(_function_schemas(tools)) == {"g"}
