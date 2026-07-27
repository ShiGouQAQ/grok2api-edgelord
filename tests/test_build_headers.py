"""Tests for build_build_headers — Grok Build (grok-shell) header builder.

Port of Go adapter.go applyHeaders() + doResponseRequest().
Default params: is_stream=True, is_trace=True (inference / streaming).
"""

from app.dataplane.proxy.adapters.headers import build_build_headers


def test_build_build_headers_required():
    headers = build_build_headers(
        access_token="test-token",
        agent_id="550e8400-e29b-41d4-a716-446655440000",
    )
    assert headers["Authorization"] == "Bearer test-token"
    assert headers["x-xai-token-auth"] == "xai-grok-cli"
    assert headers["x-grok-client-version"] == "0.2.111"
    assert headers["x-grok-client-identifier"] == "grok-shell"
    assert headers["x-grok-client-mode"] == "headless"
    assert headers["Content-Type"] == "application/json"
    assert headers["User-Agent"] == "grok-shell/0.2.111 (linux; x86_64)"

    # Default: is_stream=True → streaming Accept/Encoding
    assert headers["Accept"] == "text/event-stream"
    assert headers["Accept-Encoding"] == "identity"

    # Default: is_trace=True → inference headers
    assert headers["x-authenticateresponse"] == "authenticate-response"
    assert headers["x-grok-agent-id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert "x-grok-req-id" in headers
    assert "traceparent" in headers


def test_build_build_headers_with_session():
    headers = build_build_headers(
        access_token="t",
        agent_id="a",
        session_id="ses-123",
        model="grok-4.5",
    )
    assert headers["x-grok-session-id"] == "ses-123"
    assert headers["x-grok-conv-id"] == "ses-123"
    assert headers["x-grok-model-override"] == "grok-4.5"


def test_build_build_headers_with_turn_idx():
    headers = build_build_headers(
        access_token="t",
        agent_id="a",
        turn_idx="3",
    )
    assert headers["x-grok-turn-idx"] == "3"


def test_build_build_headers_client_version():
    headers = build_build_headers(
        access_token="t",
        agent_id="a",
        client_version="0.2.102",
    )
    assert headers["x-grok-client-version"] == "0.2.102"
    assert headers["User-Agent"] == "grok-shell/0.2.102 (linux; x86_64)"


def test_build_build_headers_traceparent_format():
    headers = build_build_headers(access_token="t", agent_id="a")
    tp = headers["traceparent"]
    assert tp.startswith("00-")
    parts = tp.split("-")
    assert len(parts) == 4
    assert parts[3] == "01"
    assert len(parts[1]) == 16
    assert len(parts[2]) == 8


def test_build_build_headers_generates_unique_req_ids():
    h1 = build_build_headers(access_token="t", agent_id="a")
    h2 = build_build_headers(access_token="t", agent_id="a")
    assert h1["x-grok-req-id"] != h2["x-grok-req-id"]


def test_build_build_headers_optional_absent():
    headers = build_build_headers(access_token="t", agent_id="a")
    assert "x-grok-session-id" not in headers
    assert "x-grok-conv-id" not in headers
    assert "x-grok-model-override" not in headers
    assert "x-grok-turn-idx" not in headers


# ---------------------------------------------------------------------------
# is_stream variants
# ---------------------------------------------------------------------------


def test_build_build_headers_stream_true():
    headers = build_build_headers(access_token="t", agent_id="a", is_stream=True)
    assert headers["Accept"] == "text/event-stream"
    assert headers["Accept-Encoding"] == "identity"


def test_build_build_headers_stream_false():
    headers = build_build_headers(access_token="t", agent_id="a", is_stream=False)
    assert headers["Accept"] == "application/json"
    assert headers["Accept-Encoding"] == "gzip"


# ---------------------------------------------------------------------------
# is_trace variants
# ---------------------------------------------------------------------------


def test_build_build_headers_trace_true():
    headers = build_build_headers(access_token="t", agent_id="a", is_trace=True)
    assert "x-authenticateresponse" in headers
    assert "x-grok-agent-id" in headers
    assert "x-grok-req-id" in headers
    assert "traceparent" in headers


def test_build_build_headers_trace_false():
    headers = build_build_headers(access_token="t", agent_id="a", is_trace=False)
    assert "x-authenticateresponse" not in headers
    assert "x-grok-req-id" not in headers
    assert "traceparent" not in headers
    # agent-id still absent when trace=False (Go sets x-userid / x-email instead)
    assert "x-grok-agent-id" not in headers


def test_build_build_headers_no_session_when_trace_false():
    """Session headers are inference-only (trace=True)."""
    headers = build_build_headers(
        access_token="t", agent_id="a", session_id="ses-x", is_trace=False
    )
    assert "x-grok-session-id" not in headers
    assert "x-grok-conv-id" not in headers
