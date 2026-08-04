"""Tests for integer tool-argument normalization.

Port of Go 8b5c1ed6 + e3af4fce (cli/responses_arguments.go):
pure-string decimal normalizer, schema walker, streaming buffer guards.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.dataplane.reverse.protocol.responses_input import normalize_input_items
from app.dataplane.reverse.protocol.responses_response import (
    normalize_response_json,
    rewrite_function_call,
)
from app.dataplane.reverse.protocol.tool_parser import (
    FunctionArgumentsBuffer,
    MAX_BUFFERED_FUNCTION_ARGUMENTS_BYTES,
    MAX_EXACT_JSON_INTEGER,
    MAX_TOTAL_BUFFERED_FUNCTION_ARGS_BYTES,
    normalize_function_arguments,
    normalize_integral_number,
    parse_tool_calls,
    release_buffered_function_arguments,
    schema_contains_reachable_integer,
    schema_requires_integer,
)


# ---------------------------------------------------------------------------
# normalize_integral_number — pure string decimal arithmetic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("60000.0", "60000"),
        ("6e4", "60000"),
        ("1000e-3", "1"),
        ("1.2300e2", "123"),
        ("-0.0", "0"),
        ("-0", "0"),
        ("0.0", "0"),
        ("0e1000000000", "0"),
        ("12.00", "12"),
        ("1.0e2", "100"),
        ("5.5e1", "55"),
        ("-12.0", "-12"),
        ("9007199254740991.0", "9007199254740991"),
        ("-9007199254740991.0", "-9007199254740991"),
    ],
)
def test_normalize_integral_number_rewrites(raw: str, expected: str):
    assert normalize_integral_number(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "12",  # already canonical, no .eE spelling
        "12.5",  # non-integral fraction
        "1e-1",  # non-integral
        "9007199254740990.5",  # non-integral fraction
        "9007199254740992.0",  # > 2^53 - 1
        "1e1000000000",  # exponent digits > 9
        "x12.0",  # malformed
        "1.2.3",  # malformed
        "1e",  # empty exponent
    ],
)
def test_normalize_integral_number_returns_none(raw: str):
    assert normalize_integral_number(raw) is None


def test_normalize_integral_number_256_char_cap():
    # 300-digit integer in float spelling with huge mantissa → capped
    huge = "1" + "0" * 299 + ".0"
    assert len(huge) > 256
    assert normalize_integral_number(huge) is None
    # exactly within cap still works
    ok = "9" * 200 + ".0"
    assert len(ok) <= 256
    # 200 nines exceeds 2^53-1 lexically → None
    assert normalize_integral_number(ok) is None


def test_normalize_integral_number_2_53_boundary():
    assert normalize_integral_number(str(MAX_EXACT_JSON_INTEGER) + ".0") == str(
        MAX_EXACT_JSON_INTEGER
    )
    assert normalize_integral_number(str(MAX_EXACT_JSON_INTEGER + 1) + ".0") is None


# ---------------------------------------------------------------------------
# schema_requires_integer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "integer"}, True),
        ({"type": "number"}, False),
        ({"type": "string"}, False),
        ({"type": ["integer", "null"]}, True),
        ({"type": ["integer", "number"]}, False),  # number present excludes
        ({"type": ["number"]}, False),
        ({"type": ["string", "integer"]}, True),
        ({"properties": {"a": {"type": "integer"}}}, False),  # non-local type
        ({"$ref": "#/$defs/x"}, False),
        ("integer", False),
        (None, False),
    ],
)
def test_schema_requires_integer(schema, expected):
    assert schema_requires_integer(schema) is expected


# ---------------------------------------------------------------------------
# normalize_function_arguments — whole-tree walk
# ---------------------------------------------------------------------------

INTEGER_SCHEMA = {
    "type": "object",
    "properties": {
        "temperature": {"type": "integer"},
        "ratio": {"type": "number"},
        "items": {
            "type": "array",
            "items": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        },
    },
}


def test_integer_float_coerced_to_int():
    normalized, changed = normalize_function_arguments(
        '{"temperature":12.0}',
        {"type": "object", "properties": {"temperature": {"type": "integer"}}},
    )
    assert changed is True
    assert json.loads(normalized) == {"temperature": 12}
    assert isinstance(json.loads(normalized)["temperature"], int)


def test_non_integral_float_unchanged():
    schema = {"type": "object", "properties": {"temperature": {"type": "integer"}}}
    arguments = '{"temperature":12.5}'
    normalized, changed = normalize_function_arguments(arguments, schema)
    assert changed is False
    assert normalized == arguments


def test_number_schema_float_preserved():
    schema = {"type": "object", "properties": {"ratio": {"type": "number"}}}
    arguments = '{"ratio":2.0}'
    normalized, changed = normalize_function_arguments(arguments, schema)
    assert changed is False
    assert normalized == arguments


def test_mixed_tree_only_integers_rewritten():
    arguments = '{"temperature":12.0,"ratio":2.0,"items":[1.0,null,2.5]}'
    normalized, changed = normalize_function_arguments(arguments, INTEGER_SCHEMA)
    assert changed is True
    assert json.loads(normalized) == {
        "temperature": 12,
        "ratio": 2.0,
        "items": [1, None, 2.5],
    }


def test_exponent_form_rewritten():
    schema = {"type": "object", "properties": {"temperature": {"type": "integer"}}}
    normalized, changed = normalize_function_arguments('{"temperature":1e2}', schema)
    assert changed is True
    assert json.loads(normalized) == {"temperature": 100}


def test_huge_number_passthrough():
    schema = {"type": "object", "properties": {"temperature": {"type": "integer"}}}
    arguments = '{"temperature":9007199254740992.0}'
    normalized, changed = normalize_function_arguments(arguments, schema)
    assert changed is False
    assert normalized == arguments


def test_empty_or_invalid_json_passthrough():
    schema = {"type": "object", "properties": {"temperature": {"type": "integer"}}}
    for arguments in ("", "   ", "not json", '{"temperature":12.0} trailing'):
        normalized, changed = normalize_function_arguments(arguments, schema)
        assert changed is False
        assert normalized == arguments


def test_non_dict_schema_passthrough():
    arguments = '{"temperature":12.0}'
    normalized, changed = normalize_function_arguments(arguments, None)
    assert changed is False
    assert normalized == arguments


def test_ref_at_root_resolved():
    schema = {
        "$ref": "#/$defs/arguments",
        "$defs": {
            "arguments": {
                "type": "object",
                "properties": {"timeout_ms": {"type": "integer"}},
            }
        },
    }
    normalized, changed = normalize_function_arguments('{"timeout_ms":6e4}', schema)
    assert changed is True
    assert json.loads(normalized) == {"timeout_ms": 60000}


def test_ref_in_properties_resolved():
    schema = {
        "type": "object",
        "properties": {
            "value": {"$ref": "#/$defs/count"},
        },
        "$defs": {"count": {"type": "integer"}},
    }
    normalized, changed = normalize_function_arguments('{"value":1.0}', schema)
    assert changed is True
    assert json.loads(normalized) == {"value": 1}


def test_ref_cycle_does_not_hang():
    schema = {
        "$ref": "#/$defs/loop",
        "$defs": {
            "loop": {
                "type": "object",
                "properties": {
                    "next": {"$ref": "#/$defs/loop"},
                    "n": {"type": "integer"},
                },
            }
        },
    }
    # deeply nested cycle — depth cap stops the walk, inner n normalized
    normalized, changed = normalize_function_arguments(
        '{"next":{"next":{"n":1.0}}}', schema
    )
    assert changed is True
    assert json.loads(normalized) == {"next": {"next": {"n": 1}}}


def test_depth_cap_bounds_walk():
    # Each nesting level costs 2 depth steps (property + $ref), so
    # levels ≤ 32 normalize (depth ≤ 64) and deeper ones stay untouched.
    deep: dict[str, Any] = {
        "type": "object",
        "properties": {"next": {"$ref": "#/$defs/deep"}},
    }
    deep["$defs"] = {"deep": deep}
    shallow: dict[str, Any] = {
        "type": "object",
        "properties": {"next": {"$ref": "#/$defs/deep"}, "n": {"type": "integer"}},
    }
    shallow["$defs"] = {"deep": shallow}

    args = _nested(20, {"n": 2.0})  # depth ~42 → within cap
    normalized, changed = normalize_function_arguments(json.dumps(args), shallow)
    assert changed is True
    assert _leaf_value(json.loads(normalized)) == 2

    args = _nested(60, {"n": 2.0})  # depth ~122 → beyond cap
    normalized, changed = normalize_function_arguments(json.dumps(args), deep)
    assert changed is False
    assert normalized == json.dumps(args)


def _nested(depth: int, leaf: Any) -> dict[str, Any]:
    out: dict[str, Any] = leaf
    for _ in range(depth):
        out = {"next": out}
    return out


def _leaf_value(node) -> Any:
    while isinstance(node, dict) and "next" in node:
        node = node["next"]
    return node.get("n")


# ---------------------------------------------------------------------------
# schema_contains_reachable_integer — bounded reachability walker
# ---------------------------------------------------------------------------


def test_reachable_integer_detected_through_refs():
    schema = {
        "type": "object",
        "properties": {"value": {"$ref": "#/$defs/count"}},
        "$defs": {"count": {"type": "integer"}},
    }
    assert schema_contains_reachable_integer(schema) is True


def test_unreachable_integer_not_detected():
    schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "$defs": {"unused": {"type": "integer"}},
    }
    assert schema_contains_reachable_integer(schema) is False


def test_reachable_integer_detected_in_arrays():
    schema = {
        "type": "array",
        "prefixItems": [{"type": "string"}, {"type": "integer"}],
    }
    assert schema_contains_reachable_integer(schema) is True


def test_reachable_integer_detected_in_combinators():
    schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    assert schema_contains_reachable_integer(schema) is True
    schema = {"oneOf": [{"type": "number"}, {"type": "integer"}]}
    assert schema_contains_reachable_integer(schema) is True


def test_reachable_integer_detected_in_additional_properties():
    schema = {
        "type": "object",
        "additionalProperties": {"type": "integer"},
    }
    assert schema_contains_reachable_integer(schema) is True


def test_reachable_integer_cycle_guard():
    schema = {
        "type": "object",
        "properties": {"next": {"$ref": "#/$defs/node"}},
        "$defs": {
            "node": {
                "type": "object",
                "properties": {"next": {"$ref": "#/$defs/node"}},
            }
        },
    }
    assert schema_contains_reachable_integer(schema) is False  # no integer, no hang


# ---------------------------------------------------------------------------
# parse_tool_calls schema-aware integration (streaming reconstruction seam)
# ---------------------------------------------------------------------------

XML_CALL = (
    "<tool_calls><tool_call><tool_name>set_temp</tool_name>"
    '<parameters>{"temperature":12.0}</parameters></tool_call></tool_calls>'
)


def test_parse_tool_calls_normalizes_with_schema():
    schemas = {
        "set_temp": {
            "type": "object",
            "properties": {"temperature": {"type": "integer"}},
        }
    }
    result = parse_tool_calls(XML_CALL, schemas=schemas)
    assert len(result.calls) == 1
    assert json.loads(result.calls[0].arguments) == {"temperature": 12}


def test_parse_tool_calls_no_schema_unchanged():
    result = parse_tool_calls(XML_CALL)
    assert len(result.calls) == 1
    assert json.loads(result.calls[0].arguments) == {"temperature": 12.0}


def test_parse_tool_calls_unknown_name_unchanged():
    result = parse_tool_calls(XML_CALL, schemas={"other": {"type": "object"}})
    assert len(result.calls) == 1
    assert json.loads(result.calls[0].arguments) == {"temperature": 12.0}


def test_parse_tool_calls_schema_without_integer_unchanged():
    schemas = {
        "set_temp": {
            "type": "object",
            "properties": {"temperature": {"type": "number"}},
        }
    }
    result = parse_tool_calls(XML_CALL, schemas=schemas)
    assert len(result.calls) == 1
    assert json.loads(result.calls[0].arguments) == {"temperature": 12.0}


# ---------------------------------------------------------------------------
# Streaming buffer-bomb guards (1MB per call, 4MB global)
# ---------------------------------------------------------------------------


def test_buffer_feed_release_roundtrip():
    buf = FunctionArgumentsBuffer()
    assert buf.feed('{"a":') is None
    assert buf.feed("1.0}") is None
    assert buf.text() == '{"a":1.0}'
    release_buffered_function_arguments(buf)
    assert buf.text() == ""
    assert buf.passthrough is False


def test_per_call_1mb_passthrough(monkeypatch):
    monkeypatch.setattr(
        "app.dataplane.reverse.protocol.tool_parser._buffered_function_argument_bytes",
        0,
    )
    buf = FunctionArgumentsBuffer()
    chunk = "a" * (MAX_BUFFERED_FUNCTION_ARGUMENTS_BYTES // 2)
    assert buf.feed(chunk) is None  # 512KB buffered fine
    assert buf.feed(chunk) is None  # exactly 1MB → still buffered
    assert buf.passthrough is False
    flushed = buf.feed("b")  # 1MB + 1 > cap → passthrough
    assert flushed == chunk + chunk  # buffered content flushed verbatim
    assert buf.passthrough is True
    assert buf.text() == ""  # buffer released
    assert buf.feed("c") is None  # passthrough absorbs without buffering


def test_global_4mb_passthrough(monkeypatch):
    monkeypatch.setattr(
        "app.dataplane.reverse.protocol.tool_parser._buffered_function_argument_bytes",
        0,
    )
    # Shrink the global cap so two small buffers exhaust it
    monkeypatch.setattr(
        "app.dataplane.reverse.protocol.tool_parser.MAX_TOTAL_BUFFERED_FUNCTION_ARGS_BYTES",
        100,
    )
    first = FunctionArgumentsBuffer()
    second = FunctionArgumentsBuffer()
    assert first.feed("x" * 60) is None  # 60 global bytes
    assert first.passthrough is False
    flushed = first.feed("x" * 60)  # 120 > 100 global cap → overflow
    assert flushed == "x" * 60  # buffered text flushed verbatim
    assert first.passthrough is True
    assert first.text() == ""  # buffer released
    assert second.feed("y" * 10) is None  # global counter released → fresh budget
    assert second.passthrough is False


def test_global_counter_released_on_completion(monkeypatch):
    monkeypatch.setattr(
        "app.dataplane.reverse.protocol.tool_parser._buffered_function_argument_bytes",
        0,
    )
    buf = FunctionArgumentsBuffer()
    buf.feed("a" * 100)
    buf.feed("b" * 50)
    module = "app.dataplane.reverse.protocol.tool_parser"
    import importlib

    tp = importlib.import_module(module)
    assert tp._buffered_function_argument_bytes == 150
    release_buffered_function_arguments(buf)
    assert tp._buffered_function_argument_bytes == 0


def test_default_global_caps():
    assert MAX_BUFFERED_FUNCTION_ARGUMENTS_BYTES == 1 << 20
    assert MAX_TOTAL_BUFFERED_FUNCTION_ARGS_BYTES == 4 << 20


# ---------------------------------------------------------------------------
# Responses API integration (stored outputs + input history)
# ---------------------------------------------------------------------------

WAIT_SCHEMA = {
    "type": "object",
    "properties": {"timeout_ms": {"type": "integer"}},
}
WAIT_SCHEMAS = {"wait_agent": WAIT_SCHEMA}


def test_response_json_normalizes_integer_arguments():
    body = {
        "output": [
            {
                "type": "function_call",
                "name": "wait_agent",
                "arguments": '{"timeout_ms":60000.0}',
            }
        ]
    }
    result = normalize_response_json(body, schemas=WAIT_SCHEMAS)
    assert json.loads(result["output"][0]["arguments"]) == {"timeout_ms": 60000}


def test_response_json_without_schemas_unchanged():
    body = {
        "output": [
            {
                "type": "function_call",
                "name": "wait_agent",
                "arguments": '{"timeout_ms":60000.0}',
            }
        ]
    }
    result = normalize_response_json(body)
    assert result["output"][0]["arguments"] == '{"timeout_ms":60000.0}'


def test_rewrite_function_call_normalizes_arguments():
    item = {"name": "wait_agent", "arguments": '{"timeout_ms":6e4}'}
    result = rewrite_function_call(item, schemas=WAIT_SCHEMAS)
    assert json.loads(result["arguments"]) == {"timeout_ms": 60000}


def test_rewrite_function_call_namespace_preserves_normalized_arguments():
    schemas = {"ns__wait_agent": WAIT_SCHEMA}
    result = rewrite_function_call(
        {"name": "ns__wait_agent", "arguments": '{"timeout_ms":60000.0}'},
        schemas=schemas,
    )
    assert result["name"] == "wait_agent"
    assert json.loads(result["arguments"]) == {"timeout_ms": 60000}


def test_input_function_call_normalized_with_schema():
    items = [
        {
            "type": "function_call",
            "name": "wait_agent",
            "arguments": '{"timeout_ms":1.0}',
        }
    ]
    result = normalize_input_items(items, schemas=WAIT_SCHEMAS)
    assert json.loads(result[0]["arguments"]) == {"timeout_ms": 1}


def test_input_function_call_no_schema_unchanged():
    items = [
        {
            "type": "function_call",
            "name": "wait_agent",
            "arguments": '{"timeout_ms":1.0}',
        }
    ]
    result = normalize_input_items(items)
    assert result[0]["arguments"] == '{"timeout_ms":1.0}'


def test_input_non_string_arguments_unchanged():
    items = [
        {
            "type": "function_call",
            "name": "wait_agent",
            "arguments": {"timeout_ms": 1.0},
        }
    ]
    result = normalize_input_items(items, schemas=WAIT_SCHEMAS)
    assert result[0]["arguments"] == {"timeout_ms": 1.0}
