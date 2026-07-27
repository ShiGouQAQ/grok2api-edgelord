"""Tests for gRPC-web protobuf wire format codec.

Tests cover all functions in app.platform.auth.grpc_web_codec:
- Varint encoding/decoding
- Protobuf field tag and string encoding
- gRPC-web request framing
- gRPC-web response parsing (messages and trailers)
- Protobuf message decoding
"""

from app.platform.auth.grpc_web_codec import (
    encode_varint,
    read_varint,
    encode_string,
    frame_request,
    parse_response,
)


class TestGRPCWebCodec:
    """Test the pure-Python protobuf/gRPC-web codec functions."""

    def test_encode_varint_small(self) -> None:
        assert encode_varint(0) == b"\x00"
        assert encode_varint(1) == b"\x01"
        assert encode_varint(127) == b"\x7f"

    def test_encode_varint_large(self) -> None:
        result = encode_varint(300)
        assert result == b"\xac\x02"

    def test_encode_varint_negative_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="non-negative"):
            encode_varint(-1)

    def test_read_varint_single_byte(self) -> None:
        val, pos = read_varint(b"\x2a\x00", 0)
        assert val == 42
        assert pos == 1

    def test_read_varint_multi_byte(self) -> None:
        val, pos = read_varint(b"\xac\x02", 0)
        assert val == 300
        assert pos == 2

    def test_grpc_encode_string(self) -> None:
        result = encode_string(1, "hello")
        assert result.startswith(b"\x0a")
        assert result.endswith(b"hello")

    def test_grpc_frame_request(self) -> None:
        msg = b"test message"
        framed = frame_request(msg)
        assert framed[0] == 0x00
        assert len(framed) == 1 + 4 + len(msg)
        assert int.from_bytes(framed[1:5], "big") == len(msg)

    def test_grpc_parse_response_trailers(self) -> None:
        trailer_data = b"grpc-status: 0\r\ngrpc-message: OK\r\n"
        body = b"\x80" + len(trailer_data).to_bytes(4, "big") + trailer_data
        parsed = parse_response(body)
        assert parsed["grpc_status"] == 0
        assert parsed["trailers"]["grpc-status"] == "0"

    def test_grpc_parse_response_message(self) -> None:
        msg = encode_string(1, "https://x.ai/")
        framed = b"\x00" + len(msg).to_bytes(4, "big") + msg
        parsed = parse_response(framed)
        assert len(parsed["messages"]) == 1
        assert parsed["grpc_status"] is None

    def test_grpc_parse_response_trailers_no_grpc_status(self) -> None:
        trailer_data = b"some-other-header: value\r\n"
        body = b"\x80" + len(trailer_data).to_bytes(4, "big") + trailer_data
        parsed = parse_response(body)
        assert parsed["grpc_status"] is None
