"""stream_console_chat DPoP (RFC 9449) wiring tests.

The DPoP protocol itself is covered in test_dpop.py (sibling module); these
tests verify the chat path wiring: DPoP headers on the outbound POST, 401
session rebuild, and token-endpoint failure semantics. No real network: the
DPoP manager factory, the proxy runtime and the HTTP session are all mocked.
"""

import time
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.control.proxy.models import ProxyFeedbackKind
from app.dataplane.reverse.protocol.dpop import (
    DPoPError,
    DPoPSession,
    DPoPTokenEndpointError,
    dpop_session_cache_key,
    public_dpop_jwk,
)
from app.dataplane.reverse.protocol.xai_console_chat import stream_console_chat
from app.dataplane.reverse.runtime.endpoint_table import CONSOLE_BASE, CONSOLE_RESPONSES
from app.platform.errors import UpstreamError

CONSOLE_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)


class _Profile:
    user_agent = CONSOLE_UA
    browser = "chrome120"
    cf_cookies = ""
    cf_clearance = ""


def _fake_session(access_token: str = "at-123") -> DPoPSession:
    key = ec.generate_private_key(ec.SECP256R1())
    return DPoPSession(
        access_token=access_token,
        private_key=key,
        public_jwk=public_dpop_jwk(key),
        expires_at=int(time.time() * 1000) + 3_600_000,
    )


def _fake_manager(*sessions: DPoPSession) -> MagicMock:
    manager = MagicMock()
    manager.get_or_fetch = AsyncMock(side_effect=list(sessions))
    manager.invalidate = Mock()
    return manager


def _response(
    status: int = 200,
    *,
    sse_lines: list[bytes] | None = None,
    body: str = '{"error": "boom"}',
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    if sse_lines is not None:

        async def _aiter_lines():
            for line in sse_lines:
                yield line

        resp.aiter_lines = _aiter_lines
    else:

        async def _atext():
            return body

        resp.atext = _atext
    return resp


def _session_mock(*responses: MagicMock) -> AsyncMock:
    session = AsyncMock()
    session.post.side_effect = list(responses)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


def _sse_ok_lines() -> list[bytes]:
    return [
        b"event: response.output_text.delta",
        b'data: {"delta": "hi"}',
        b"event: response.completed",
        b'data: {"response": {"usage": {"total_tokens": 3}}}',
    ]


@contextmanager
def _patched(manager: MagicMock, session: AsyncMock):
    proxy = AsyncMock()
    lease = MagicMock()
    proxy.acquire.return_value = lease
    with (
        patch("app.dataplane.proxy.get_proxy_runtime", return_value=proxy),
        patch(
            "app.dataplane.proxy.adapters.session.ResettableSession",
            return_value=session,
        ),
        patch(
            "app.dataplane.proxy.adapters.session.build_session_kwargs", return_value={}
        ),
        patch(
            "app.dataplane.proxy.adapters.headers._resolve_profile",
            return_value=_Profile(),
        ),
        patch(
            "app.dataplane.reverse.protocol.xai_console_chat._get_dpop_manager",
            return_value=manager,
        ),
    ):
        yield proxy, lease


@pytest.mark.asyncio
async def test_success_sends_dpop_headers_and_streams():
    session = _fake_session("at-123")
    manager = _fake_manager(session)
    http = _session_mock(_response(sse_lines=_sse_ok_lines()))

    with _patched(manager, http):
        events = [
            ev
            async for ev in stream_console_chat(
                "sso-tok", {"model": "grok-4.3", "input": []}
            )
        ]

    assert events == [
        ("response.output_text.delta", '{"delta": "hi"}'),
        ("response.completed", '{"response": {"usage": {"total_tokens": 3}}}'),
    ]
    assert http.post.await_count == 1
    call = http.post.call_args
    assert call.args[0] == CONSOLE_RESPONSES
    assert call.kwargs["stream"] is True
    headers = call.kwargs["headers"]
    assert headers["Authorization"] == "DPoP at-123"
    assert headers["DPoP"].count(".") == 2  # ES256 proof JWT
    assert headers["Cache-Control"] == "no-cache"
    assert headers["Pragma"] == "no-cache"
    manager.get_or_fetch.assert_awaited_once_with(CONSOLE_BASE, 0, 0, "sso-tok")


@pytest.mark.asyncio
async def test_401_invalidates_refetches_and_retries():
    s1, s2 = _fake_session("at-1"), _fake_session("at-2")
    manager = _fake_manager(s1, s2)
    http = _session_mock(
        _response(401, body='{"code": "unauthorized:dpop-required"}'),
        _response(sse_lines=_sse_ok_lines()),
    )

    with _patched(manager, http):
        events = [
            ev
            async for ev in stream_console_chat(
                "sso-tok", {"model": "grok-4.3", "input": []}
            )
        ]

    assert http.post.await_count == 2
    first = http.post.call_args_list[0].kwargs["headers"]
    second = http.post.call_args_list[1].kwargs["headers"]
    assert first["Authorization"] == "DPoP at-1"
    assert second["Authorization"] == "DPoP at-2"
    manager.invalidate.assert_called_once_with(
        dpop_session_cache_key(CONSOLE_BASE, 0, 0, "sso-tok"), "at-1"
    )
    assert manager.get_or_fetch.await_count == 2
    assert events == [
        ("response.output_text.delta", '{"delta": "hi"}'),
        ("response.completed", '{"response": {"usage": {"total_tokens": 3}}}'),
    ]


@pytest.mark.asyncio
async def test_second_401_raises_upstream_error():
    s1, s2 = _fake_session("at-1"), _fake_session("at-2")
    manager = _fake_manager(s1, s2)
    http = _session_mock(
        _response(401, body='{"code": "unauthorized:dpop-required"}'),
        _response(401, body='{"code": "unauthorized:dpop-required"}'),
    )

    with _patched(manager, http):
        with pytest.raises(UpstreamError):
            async for _ in stream_console_chat("sso-tok", {}):
                pass

    assert http.post.await_count == 2
    manager.invalidate.assert_called_once()
    assert manager.get_or_fetch.await_count == 2


@pytest.mark.asyncio
async def test_token_endpoint_403_with_invalidate_clearance_raises_403():
    manager = MagicMock()
    manager.get_or_fetch = AsyncMock(
        side_effect=DPoPTokenEndpointError(403, b"{}", invalidate_clearance=True)
    )
    http = _session_mock()

    with _patched(manager, http) as (proxy, _lease):
        with pytest.raises(UpstreamError) as exc_info:
            async for _ in stream_console_chat("sso-tok", {}):
                pass

    assert exc_info.value.status == 403
    assert http.post.await_count == 0  # no outbound attempt
    proxy.feedback.assert_called_once()
    assert proxy.feedback.call_args[0][1].kind == ProxyFeedbackKind.FORBIDDEN


@pytest.mark.asyncio
async def test_token_endpoint_generic_error_raises_502():
    manager = MagicMock()
    manager.get_or_fetch = AsyncMock(
        side_effect=DPoPTokenEndpointError(500, b"{}", invalidate_clearance=False)
    )
    http = _session_mock()

    with _patched(manager, http) as (proxy, _lease):
        with pytest.raises(UpstreamError) as exc_info:
            async for _ in stream_console_chat("sso-tok", {}):
                pass

    assert exc_info.value.status == 502
    assert http.post.await_count == 0
    proxy.feedback.assert_called_once()
    assert proxy.feedback.call_args[0][1].kind == ProxyFeedbackKind.TRANSPORT_ERROR


@pytest.mark.asyncio
async def test_dpop_error_raises_502():
    manager = MagicMock()
    manager.get_or_fetch = AsyncMock(side_effect=DPoPError("token claims invalid"))
    http = _session_mock()

    with _patched(manager, http) as (proxy, _lease):
        with pytest.raises(UpstreamError) as exc_info:
            async for _ in stream_console_chat("sso-tok", {}):
                pass

    assert exc_info.value.status == 502
    assert http.post.await_count == 0
    proxy.feedback.assert_called_once()
