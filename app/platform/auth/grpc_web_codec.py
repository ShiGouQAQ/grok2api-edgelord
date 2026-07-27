"""Pure-Python gRPC-web protobuf wire format codec.

This module provides encoding/decoding utilities for the gRPC-web transport
protocol and protobuf wire format, with no external dependencies.

Wire format reference:
  - Protobuf: https://protobuf.dev/programming-guides/encoding/
  - gRPC-web: https://github.com/grpc/grpc/blob/master/doc/PROTOCOL-WEB.md

gRPC-web framing:
  Each frame is: flag (1 byte) + length (4 bytes big-endian) + payload.
  flag & 0x80 == trailers (key:value\\r\\n), else protobuf message.

Protobuf wire types:
  - WT_VARINT (0): variable-length integer encoding
  - WT_FIXED64 (1): 8-byte fixed-width value
  - WT_LEN (2): length-delimited value (string, bytes, embedded message)
  - WT_FIXED32 (5): 4-byte fixed-width value

Ported from GrokRegisterAgent/register/cpa_grpcweb.py.
"""

from __future__ import annotations

import struct
from typing import Any

__all__ = [
    "WT_VARINT",
    "WT_FIXED64",
    "WT_LEN",
    "WT_FIXED32",
    "encode_varint",
    "read_varint",
    "tag",
    "encode_string",
    "frame_request",
    "parse_response",
    "decode_message",
]

# ─── Protobuf wire types ─────────────────────────────────────────────────

WT_VARINT = 0
WT_FIXED64 = 1
WT_LEN = 2
WT_FIXED32 = 5


# ─── Varint encoding/decoding ────────────────────────────────────────────


def encode_varint(value: int) -> bytes:
    """Encode an integer as a protobuf varint.

    Args:
        value: Non-negative integer to encode.

    Returns:
        Varint-encoded bytes.

    Raises:
        ValueError: If value is negative.
    """
    if value < 0:
        raise ValueError("varint must be non-negative")
    buf = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            buf.append(byte | 0x80)
        else:
            buf.append(byte)
            return bytes(buf)


def read_varint(data: bytes, i: int) -> tuple[int, int]:
    """Read a varint from *data* starting at offset *i*.

    Args:
        data: The byte string to read from.
        i: Starting offset.

    Returns:
        (value, new_offset) tuple.
    """
    result = 0
    shift = 0
    while True:
        b = data[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7


# ─── Protobuf field encoding ──────────────────────────────────────────────


def tag(field_no: int, wire_type: int) -> bytes:
    """Encode a protobuf field tag (field number << 3 | wire type) as varint."""
    return encode_varint((field_no << 3) | wire_type)


def encode_string(field_no: int, text: str) -> bytes:
    """Encode a protobuf length-delimited string field.

    Args:
        field_no: Protobuf field number.
        text: UTF-8 string value.

    Returns:
        Encoded bytes (tag + length varint + UTF-8 data).
    """
    raw = text.encode("utf-8")
    return tag(field_no, WT_LEN) + encode_varint(len(raw)) + raw


# ─── gRPC-web framing ────────────────────────────────────────────────────


def frame_request(message: bytes) -> bytes:
    """Frame a message for gRPC-web transport.

    Format: flag (0x00) + 4-byte big-endian length + payload.

    Args:
        message: The serialized protobuf message bytes.

    Returns:
        Framed bytes ready for gRPC-web transport.
    """
    return b"\x00" + struct.pack(">I", len(message)) + message


# ─── Protobuf message decoding ────────────────────────────────────────────


def decode_message(data: bytes) -> list[dict[str, Any]]:
    """Decode a protobuf message into a list of field dicts.

    Each field dict contains:
      - "field": int — field number
      - "type": str — one of "varint", "fixed64", "string", "bytes", "fixed32"
      - "value"/"hex"/"len": the decoded value

    Args:
        data: Raw protobuf-encoded message bytes.

    Returns:
        List of field dictionaries.
    """
    fields: list[dict[str, Any]] = []
    i = 0
    n = len(data)
    while i < n:
        tag_val, i = read_varint(data, i)
        field_no = tag_val >> 3
        wt = tag_val & 0x07
        if wt == WT_VARINT:
            val, i = read_varint(data, i)
            fields.append({"field": field_no, "type": "varint", "value": val})
        elif wt == WT_FIXED64:
            chunk = data[i : i + 8]
            i += 8
            fields.append({"field": field_no, "type": "fixed64", "hex": chunk.hex()})
        elif wt == WT_LEN:
            ln, i = read_varint(data, i)
            chunk = data[i : i + ln]
            i += ln
            try:
                s = chunk.decode("utf-8")
                if s.isprintable():
                    fields.append({"field": field_no, "type": "string", "value": s})
                    continue
            except UnicodeDecodeError:
                pass
            fields.append(
                {"field": field_no, "type": "bytes", "hex": chunk.hex(), "len": ln}
            )
        elif wt == WT_FIXED32:
            chunk = data[i : i + 4]
            i += 4
            fields.append({"field": field_no, "type": "fixed32", "hex": chunk.hex()})
        else:
            raise ValueError(f"unsupported wire type {wt} at offset {i}")
    return fields


# ─── gRPC-web response parsing ────────────────────────────────────────────


def parse_response(body: bytes) -> dict[str, Any]:
    """Parse a gRPC-web response into messages and trailers.

    The response body consists of one or more frames. Each frame has:
      - flag (1 byte): 0x80 = trailers, 0x00 = data
      - length (4 bytes, big-endian)
      - payload (length bytes)

    Args:
        body: Raw gRPC-web response body bytes.

    Returns:
        Dict with keys:
          - "messages": list[list[dict]] — protobuf field lists per frame
          - "trailers": dict[str, str] — parsed trailer headers
          - "grpc_status": int | None — parsed gRPC status code
    """
    messages: list[list[dict[str, Any]]] = []
    trailers: dict[str, str] = {}
    i = 0
    n = len(body)
    while i + 5 <= n:
        flag = body[i]
        length = struct.unpack(">I", body[i + 1 : i + 5])[0]
        payload = body[i + 5 : i + 5 + length]
        i += 5 + length
        if flag & 0x80:
            for line in payload.decode("utf-8", "replace").split("\r\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    trailers[k.strip().lower()] = v.strip()
        else:
            messages.append(decode_message(payload))

    grpc_status: int | None = (
        int(trailers["grpc-status"]) if "grpc-status" in trailers else None
    )
    return {"messages": messages, "trailers": trailers, "grpc_status": grpc_status}
