"""Tool call parser — extract structured tool calls from model text output.

Tries multiple formats in priority order:
  1. <tool_calls> XML  (canonical format we inject)
  2. JSON envelope {"tool_calls": [...]}
  3. JSON array  [{"name": ..., "input": ...}]
  4. Alternative XML tags (<function_call>, <invoke>)

Returns a list of ParsedToolCall dataclasses.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import orjson


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ParsedToolCall:
    call_id: str
    name: str
    arguments: str  # always a JSON string

    @staticmethod
    def make(name: str, arguments: Any) -> "ParsedToolCall":
        call_id = f"call_{int(time.time() * 1000)}{os.urandom(3).hex()}"
        if isinstance(arguments, str):
            args_str = arguments
        else:
            try:
                # orjson raises on non-finite floats (instead of emitting
                # literal NaN) — the except below degrades to "{}". Stricter.
                args_str = orjson.dumps(arguments).decode()
            except (TypeError, ValueError):
                args_str = "{}"
        return ParsedToolCall(call_id=call_id, name=name, arguments=args_str)


@dataclass
class ParseResult:
    calls: list[ParsedToolCall] = field(default_factory=list)
    saw_tool_syntax: bool = False  # detected XML/JSON envelope even if parsing failed


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_tool_calls(
    text: str,
    available_tools: list[str] | None = None,
    schemas: dict[str, Any] | None = None,
) -> ParseResult:
    """Parse tool calls from model-generated text.

    Args:
        text: Full or partial model output text.
        available_tools: If provided, only calls whose name appears in this
                         list are accepted (case-sensitive).
        schemas: Optional map of tool name → JSON schema. When a parsed
                 call's name is present and its schema contains a reachable
                 integer constraint, the call's arguments JSON is normalized
                 (integral float spellings like 12.0 → 12) before emission.
    """
    result = ParseResult()
    if not text or not text.strip():
        return result

    # Fast path: check whether tool-call syntax is present at all
    if not _has_tool_syntax(text):
        return result
    result.saw_tool_syntax = True

    # Try parsers in priority order
    calls = (
        _parse_xml_tool_calls(text)
        or _parse_json_envelope(text)
        or _parse_json_array(text)
        or _parse_alt_xml(text)
    )

    if calls and available_tools:
        calls = [c for c in calls if c.name in available_tools]

    if calls and schemas:
        calls = [_normalize_parsed_call(c, schemas) for c in calls]

    result.calls = calls or []
    return result


def _normalize_parsed_call(
    call: ParsedToolCall, schemas: dict[str, Any]
) -> ParsedToolCall:
    """_normalize_parsed_call rewrites integral float spellings in a parsed
    call's arguments when the tool's schema requires integers.

    Schemas without any reachable integer constraint are skipped, matching
    the Go upstream behavior of only capturing integer-bearing schemas.
    """
    schema = schemas.get(call.name)
    if not isinstance(schema, dict) or not schema_contains_reachable_integer(schema):
        return call
    normalized, _ = normalize_function_arguments(call.arguments, schema)
    if normalized != call.arguments:
        call.arguments = normalized
    return call


# ---------------------------------------------------------------------------
# Syntax detection
# ---------------------------------------------------------------------------

_TOOL_SYNTAX_PATTERNS = re.compile(
    r"<tool_calls|<tool_call|<function_call|<invoke\s|"
    r'"tool_calls"\s*:|\btool_calls\b',
    re.IGNORECASE,
)


def _has_tool_syntax(text: str) -> bool:
    return bool(_TOOL_SYNTAX_PATTERNS.search(text))


# ---------------------------------------------------------------------------
# Parser 1: <tool_calls> XML (canonical)
# ---------------------------------------------------------------------------

_XML_ROOT_RE = re.compile(
    r"<tool_calls\s*>(.*?)</tool_calls\s*>", re.DOTALL | re.IGNORECASE
)
_XML_CALL_RE = re.compile(
    r"<tool_call\s*>(.*?)</tool_call\s*>", re.DOTALL | re.IGNORECASE
)
_XML_NAME_RE = re.compile(
    r"<tool_name\s*>(.*?)</tool_name\s*>", re.DOTALL | re.IGNORECASE
)
_XML_PARAMS_RE = re.compile(
    r"<parameters\s*>(.*?)</parameters\s*>", re.DOTALL | re.IGNORECASE
)


def _parse_xml_tool_calls(text: str) -> list[ParsedToolCall]:
    root_m = _XML_ROOT_RE.search(text)
    if not root_m:
        return []
    calls: list[ParsedToolCall] = []
    for call_m in _XML_CALL_RE.finditer(root_m.group(1)):
        inner = call_m.group(1)
        name_m = _XML_NAME_RE.search(inner)
        params_m = _XML_PARAMS_RE.search(inner)
        if not name_m:
            continue
        name = name_m.group(1).strip()
        params = params_m.group(1).strip() if params_m else "{}"
        parsed_args = _parse_json_tolerant(params)
        if parsed_args is None:
            continue
        calls.append(ParsedToolCall.make(name, parsed_args))
    return calls


# ---------------------------------------------------------------------------
# Parser 2: {"tool_calls": [...]} JSON envelope
# ---------------------------------------------------------------------------


def _parse_json_envelope(text: str) -> list[ParsedToolCall]:
    # Only attempt if the text literally contains "tool_calls" key
    if '"tool_calls"' not in text:
        return []
    obj = _extract_outermost_json_obj(text)
    if not isinstance(obj, dict):
        return []
    raw_calls = obj.get("tool_calls")
    if not isinstance(raw_calls, list):
        return []
    return _extract_from_call_list(raw_calls)


_JSON_DECODER = json.JSONDecoder()


def _extract_outermost_json_obj(text: str) -> Any:
    """Find and parse the first top-level JSON object in *text*.

    Uses JSONDecoder.raw_decode which handles the object boundary correctly
    without a manual bracket-depth tracker.
    """
    start = text.find("{")
    if start == -1:
        return None
    try:
        obj, _ = _JSON_DECODER.raw_decode(text, start)
        return obj
    except (json.JSONDecodeError, ValueError):
        # Attempt repair on the substring from first '{' onward
        end = text.rfind("}") + 1
        return _try_repair_json(text[start:end]) if end > start else None


# ---------------------------------------------------------------------------
# Parser 3: bare JSON array [{"name":..., "input":...}]
# ---------------------------------------------------------------------------

_JSON_ARR_RE = re.compile(r"\[[\s\S]+\]", re.DOTALL)


def _parse_json_array(text: str) -> list[ParsedToolCall]:
    m = _JSON_ARR_RE.search(text)
    if not m:
        return []
    try:
        arr = orjson.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(arr, list):
        return []
    return _extract_from_call_list(arr)


def _extract_from_call_list(items: list[Any]) -> list[ParsedToolCall]:
    calls: list[ParsedToolCall] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or item.get("tool_name") or "").strip()
        args = (
            item.get("input") or item.get("arguments") or item.get("parameters") or {}
        )
        if not name:
            continue
        calls.append(ParsedToolCall.make(name, args))
    return calls


# ---------------------------------------------------------------------------
# Parser 4: alternative XML tags (<function_call>, <invoke name="...">)
# ---------------------------------------------------------------------------

_FC_RE = re.compile(
    r"<function_call\s*>(.*?)</function_call\s*>", re.DOTALL | re.IGNORECASE
)
_INVOKE_RE = re.compile(
    r'<invoke\s+name=["\']?(\w+)["\']?\s*>(.*?)</invoke\s*>', re.DOTALL | re.IGNORECASE
)
_FC_NAME_RE = re.compile(r"<name\s*>(.*?)</name\s*>", re.DOTALL | re.IGNORECASE)
_FC_ARGS_RE = re.compile(
    r"<arguments\s*>(.*?)</arguments\s*>", re.DOTALL | re.IGNORECASE
)


def _parse_alt_xml(text: str) -> list[ParsedToolCall]:
    calls: list[ParsedToolCall] = []

    # <function_call><name>...</name><arguments>...</arguments></function_call>
    for m in _FC_RE.finditer(text):
        inner = m.group(1)
        name_m = _FC_NAME_RE.search(inner)
        args_m = _FC_ARGS_RE.search(inner)
        if not name_m:
            continue
        name = name_m.group(1).strip()
        args = _parse_json_tolerant(args_m.group(1).strip() if args_m else "{}")
        if args is None:
            continue
        calls.append(ParsedToolCall.make(name, args))

    # <invoke name="tool_name">...</invoke>
    for m in _INVOKE_RE.finditer(text):
        name = m.group(1).strip()
        inner = m.group(2)
        args = _parse_json_tolerant(inner.strip())
        if args is None:
            args = {}
        calls.append(ParsedToolCall.make(name, args))

    return calls


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def _parse_json_tolerant(s: str) -> Any:
    """Try to parse JSON; attempt light repair on failure."""
    if not s:
        return {}
    try:
        return orjson.loads(s)
    except (json.JSONDecodeError, ValueError):
        repaired = _try_repair_json(s)
        return repaired


def _try_repair_json(s: str) -> Any:
    """Very lightweight JSON repair: fix unescaped newlines inside strings."""
    try:
        # Replace literal newlines inside strings (common model output issue)
        fixed = re.sub(r"(?<!\\)\n", r"\\n", s)
        return orjson.loads(fixed)
    except (json.JSONDecodeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Integer tool-argument normalization
#
# Port of Go upstream cli/responses_arguments.go (8b5c1ed6 + e3af4fce).
# Grok can emit semantically integral numbers in float spelling (60000.0)
# where strict downstream decoders require the integer spelling (60000).
# The decimal math is pure-string; no float64 is involved in the decision.
# ---------------------------------------------------------------------------

MAX_EXACT_JSON_INTEGER = 9007199254740991  # 2^53 - 1
MAX_EXACT_JSON_INTEGER_TEXT = "9007199254740991"
MAX_NORMALIZED_NUMBER_BYTES = 256
MAX_NORMALIZE_DEPTH = 64

# Buffer-bomb guards for streamed function arguments (1MB per call, 4MB total)
MAX_BUFFERED_FUNCTION_ARGUMENTS_BYTES = 1 << 20
MAX_TOTAL_BUFFERED_FUNCTION_ARGS_BYTES = 4 << 20

_buffered_function_argument_bytes = 0  # module-level shared counter


class _NumberToken:
    """Exact spelling of a float-form JSON number, captured at parse time.

    json.loads would otherwise collapse spellings (1e2 → 100.0); the raw
    text is required for string-based integrality decisions and for faithful
    re-encoding of untouched numbers.
    """

    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text


def schema_requires_integer(schema: Any) -> bool:
    """schema_requires_integer reports whether the schema constrains the
    value to be an integer: type == "integer", or a type array that contains
    "integer" and excludes "number"."""
    if not isinstance(schema, dict):
        return False
    type_value = schema.get("type")
    if isinstance(type_value, str):
        return type_value == "integer"
    if isinstance(type_value, list):
        integer = False
        for item in type_value:
            if item == "number":
                return False
            integer = integer or item == "integer"
        return integer
    return False


def _parse_bounded_decimal_exponent(raw: str) -> int | None:
    """_parse_bounded_decimal_exponent parses an exponent with an optional
    sign, capped at 9 significant digits. Returns None when out of bounds."""
    if raw == "":
        return 0
    sign = 1
    if raw[0] in "+-":
        if raw[0] == "-":
            sign = -1
        raw = raw[1:]
        if raw == "":
            return None
    raw = raw.lstrip("0")
    if raw == "":
        return 0
    if len(raw) > 9:
        return None
    try:
        return sign * int(raw)
    except ValueError:
        return None


def normalize_integral_number(text: str) -> str | None:
    """normalize_integral_number rewrites a float-spelling number to its
    canonical integer spelling when it is semantically integral and exactly
    representable below 2^53.

    Returns None when the input is not in a changeable spelling, is not
    integral, exceeds the exact-integer range, or is malformed.
    """
    raw = text
    if len(raw) > MAX_NORMALIZED_NUMBER_BYTES:
        return None
    if raw == "-0":
        return "0"
    if not any(ch in raw for ch in ".eE"):
        return None
    mantissa, exponent_text = raw, ""
    had_exponent = False
    for i, ch in enumerate(raw):
        if ch in "eE":
            mantissa, exponent_text = raw[:i], raw[i + 1 :]
            had_exponent = True
            break
    negative = mantissa.startswith("-")
    if negative:
        mantissa = mantissa[1:]
    if "." in mantissa:
        whole, fraction = mantissa.split(".", 1)
    else:
        whole, fraction = mantissa, ""
    # Defensive: only decimal digit spellings are valid (json.Number is
    # guaranteed well-formed upstream, but this is a public string function)
    if not whole.isdigit() or (fraction and not fraction.isdigit()):
        return None
    if had_exponent and exponent_text == "":
        return None
    digits = (whole + fraction).lstrip("0")
    if not digits:
        return "0"
    exponent = _parse_bounded_decimal_exponent(exponent_text)
    if exponent is None:
        return None
    decimal_shift = exponent - len(fraction)
    if decimal_shift < 0:
        fractional_digits = -decimal_shift
        if fractional_digits > len(digits) or digits[
            len(digits) - fractional_digits :
        ].strip("0"):
            return None
        digits = digits[: len(digits) - fractional_digits].lstrip("0")
        if not digits:
            return "0"
    elif decimal_shift > 0:
        if decimal_shift > len(MAX_EXACT_JSON_INTEGER_TEXT) - len(digits):
            return None
        digits += "0" * decimal_shift
    if len(digits) > len(MAX_EXACT_JSON_INTEGER_TEXT) or (
        len(digits) == len(MAX_EXACT_JSON_INTEGER_TEXT)
        and digits > MAX_EXACT_JSON_INTEGER_TEXT
    ):
        return None
    normalized = ("-" + digits) if negative else digits
    return normalized if normalized != raw else None


def _resolve_local_schema_ref(root: dict[str, Any], ref: str) -> dict[str, Any] | None:
    """_resolve_local_schema_ref resolves a JSON pointer ref within the
    schema document itself. Non-local refs (http://...) are not supported."""
    if ref == "#":
        return root
    if not ref.startswith("#/"):
        return None
    current: Any = root
    for encoded in ref[2:].split("/"):
        segment = encoded.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
        if current is None:
            return None
    return current if isinstance(current, dict) else None


def normalize_argument_value(
    value: Any, schema: Any, root: dict[str, Any], depth: int
) -> tuple[Any, bool]:
    """normalize_argument_value walks one value against its schema, rewriting
    integral float spellings to ints where the schema requires an integer.
    Returns (value, changed). Depth is capped at 64."""
    if depth > MAX_NORMALIZE_DEPTH or not isinstance(schema, dict):
        return value, False
    changed = False

    ref = schema.get("$ref")
    if isinstance(ref, str):
        resolved = _resolve_local_schema_ref(root, ref)
        if resolved is not None:
            value, current = normalize_argument_value(value, resolved, root, depth + 1)
            changed = changed or current

    for keyword in ("allOf", "anyOf", "oneOf"):
        branches = schema.get(keyword)
        if isinstance(branches, list):
            for raw_branch in branches:
                if not isinstance(raw_branch, dict):
                    continue
                value, current = normalize_argument_value(
                    value, raw_branch, root, depth + 1
                )
                changed = changed or current

    if isinstance(value, _NumberToken) and schema_requires_integer(schema):
        normalized = normalize_integral_number(value.text)
        if normalized is not None:
            return int(normalized), True
        return value, changed

    if isinstance(value, dict):
        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else None
        additional = schema.get("additionalProperties")
        additional = additional if isinstance(additional, dict) else None
        for key, item in value.items():
            property_schema = None
            if properties is not None:
                property_schema = properties.get(key)
            if not isinstance(property_schema, dict):
                property_schema = additional
            if property_schema is None:
                continue
            normalized, current = normalize_argument_value(
                item, property_schema, root, depth + 1
            )
            if current:
                value[key] = normalized
                changed = True
    elif isinstance(value, list):
        prefix_items = schema.get("prefixItems")
        prefix_items = prefix_items if isinstance(prefix_items, list) else None
        items = schema.get("items")
        items = items if isinstance(items, dict) else None
        for index, item in enumerate(value):
            item_schema = items
            if prefix_items is not None and index < len(prefix_items):
                candidate = prefix_items[index]
                if isinstance(candidate, dict):
                    item_schema = candidate
            if item_schema is None:
                continue
            normalized, current = normalize_argument_value(
                item, item_schema, root, depth + 1
            )
            if current:
                value[index] = normalized
                changed = True
    return value, changed


def _encode_normalized(value: Any) -> str:
    """_encode_normalized serializes a normalized arguments tree, emitting
    _NumberToken spellings verbatim. Mirrors Go json.Marshal: compact
    separators, UTF-8, error on non-finite floats (NaN/Infinity)."""
    if isinstance(value, _NumberToken):
        return value.text
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # Mirror Go json.Marshal: non-finite floats abort the re-encode,
        # falling back to the original arguments string.
        if not math.isfinite(value):
            raise ValueError("non-finite float")
        return json.dumps(value)
    if isinstance(value, str):
        return orjson.dumps(value).decode()
    if isinstance(value, list):
        return "[" + ",".join(_encode_normalized(item) for item in value) + "]"
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            parts.append(orjson.dumps(key).decode() + ":" + _encode_normalized(item))
        return "{" + ",".join(parts) + "}"
    raise TypeError(f"cannot encode {type(value).__name__}")


def normalize_function_arguments(arguments: str, schema: Any) -> tuple[str, bool]:
    """normalize_function_arguments repairs semantically integral JSON
    numbers that strict downstream decoders reject for integer fields.

    Returns (normalized_json, changed). Unchanged input is returned verbatim.
    """
    if not arguments.strip():
        return arguments, False
    try:
        value = json.loads(arguments, parse_int=int, parse_float=_NumberToken)
    except (json.JSONDecodeError, ValueError, TypeError, RecursionError):
        return arguments, False
    if not isinstance(schema, dict):
        return arguments, False
    normalized, changed = normalize_argument_value(value, schema, schema, 0)
    if not changed:
        return arguments, False
    try:
        encoded = _encode_normalized(normalized)
    except (TypeError, ValueError, RecursionError):
        return arguments, False
    return encoded, True


def schema_contains_reachable_integer(schema: Any) -> bool:
    """schema_contains_reachable_integer reports whether any integer
    constraint is reachable from the schema root through $refs (with a
    cycle guard), combinators, arrays, and object properties. Depth is
    capped at 64."""
    if not isinstance(schema, dict):
        return False
    return _schema_contains_reachable_integer(schema, schema, set(), 0)


def _schema_contains_reachable_integer(
    schema: Any,
    root: dict[str, Any],
    visited_refs: set[str],
    depth: int,
) -> bool:
    if depth > MAX_NORMALIZE_DEPTH or not isinstance(schema, dict):
        return False
    if schema_requires_integer(schema):
        return True
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref not in visited_refs:
        visited_refs.add(ref)
        resolved = _resolve_local_schema_ref(root, ref)
        if resolved is not None and _schema_contains_reachable_integer(
            resolved, root, visited_refs, depth + 1
        ):
            return True
    for keyword in ("allOf", "anyOf", "oneOf", "prefixItems"):
        branches = schema.get(keyword)
        if isinstance(branches, list):
            for raw_branch in branches:
                if isinstance(raw_branch, dict) and _schema_contains_reachable_integer(
                    raw_branch, root, visited_refs, depth + 1
                ):
                    return True
    for keyword in ("items", "additionalProperties"):
        child = schema.get(keyword)
        if isinstance(child, dict) and _schema_contains_reachable_integer(
            child, root, visited_refs, depth + 1
        ):
            return True
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for raw_property in properties.values():
            if isinstance(raw_property, dict) and _schema_contains_reachable_integer(
                raw_property, root, visited_refs, depth + 1
            ):
                return True
    return False


# ---------------------------------------------------------------------------
# Streamed function-arguments buffer with bomb guards
# ---------------------------------------------------------------------------


class FunctionArgumentsBuffer:
    """Per-call buffer for streamed function_call arguments deltas.

    Caps: 1MB per call, 4MB globally (module-level shared counter). When a
    feed would exceed either cap the buffer is released and the buffer
    switches to passthrough: the caller flushes the previously buffered text
    verbatim and emits the current delta verbatim (normalization skipped).
    """

    __slots__ = ("_chunks", "_bytes", "passthrough")

    def __init__(self) -> None:
        self._chunks: list[str] = []
        self._bytes = 0
        self.passthrough = False

    def feed(self, delta: str) -> str | None:
        """feed appends a delta chunk.

        Returns None when the delta was buffered. On cap overflow, releases
        the buffered text (decrementing the global counter), switches to
        passthrough, and returns the flushed text for verbatim emission.
        """
        global _buffered_function_argument_bytes
        if self.passthrough:
            return None
        if (
            self._bytes + len(delta) > MAX_BUFFERED_FUNCTION_ARGUMENTS_BYTES
            or _buffered_function_argument_bytes + len(delta)
            > MAX_TOTAL_BUFFERED_FUNCTION_ARGS_BYTES
        ):
            flushed = self.text()
            release_buffered_function_arguments(self)
            self.passthrough = True
            return flushed
        self._chunks.append(delta)
        self._bytes += len(delta)
        _buffered_function_argument_bytes += len(delta)
        return None

    def text(self) -> str:
        """text returns the accumulated arguments text."""
        return "".join(self._chunks)

    def is_passthrough(self) -> bool:
        return self.passthrough


def release_buffered_function_arguments(buffer: FunctionArgumentsBuffer) -> None:
    """release_buffered_function_arguments frees a buffer's accumulated
    bytes from the global counter and clears it. Safe on completion,
    overflow, and empty buffers."""
    global _buffered_function_argument_bytes
    if buffer is None:
        return
    _buffered_function_argument_bytes = max(
        0, _buffered_function_argument_bytes - buffer._bytes
    )
    buffer._chunks = []
    buffer._bytes = 0


class StreamFunctionArgumentsBuffer:
    """Per-call streamed function_call arguments state machine.

    Port of Go 8b5c1ed6 responsesStreamCall. Deltas accumulate in a
    FunctionArgumentsBuffer; the last delta payload is kept so .done can
    re-emit a corrected delta carrying the full normalized arguments.
    On overflow the call falls back to passthrough and the buffered text is
    flushed verbatim.
    """

    __slots__ = ("_buffer", "last_delta")

    def __init__(self) -> None:
        self._buffer = FunctionArgumentsBuffer()
        self.last_delta: dict[str, Any] | None = None

    def feed_delta(self, delta: str, payload: dict[str, Any]) -> str | None:
        """feed_delta buffers one delta fragment.

        Returns None when buffered. On cap overflow returns the flushed
        buffered text: the caller emits it as one delta plus the current
        delta verbatim, then continues in passthrough.
        """
        flushed = self._buffer.feed(delta)
        if flushed is not None:
            self.last_delta = None
            return flushed
        self.last_delta = dict(payload)
        return None

    def is_passthrough(self) -> bool:
        return self._buffer.is_passthrough()

    def done(self, arguments: str, schema: Any) -> tuple[str, dict[str, Any] | None]:
        """done normalizes the full arguments and releases the buffer.

        Returns (normalized, corrected last-delta payload or None). The
        caller emits the corrected delta (when not None) followed by a
        .done event carrying the normalized arguments.
        """
        if not arguments:
            arguments = self._buffer.text()
        normalized, _ = normalize_function_arguments(arguments, schema)
        release_buffered_function_arguments(self._buffer)
        last_delta = self.last_delta
        self.last_delta = None
        if last_delta is not None:
            last_delta = dict(last_delta)
            last_delta["delta"] = normalized
        return normalized, last_delta
