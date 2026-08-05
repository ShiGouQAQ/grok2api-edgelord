"""stream_console_chat DPoP (RFC 9449) wiring tests.

The DPoP protocol itself is covered in test_dpop.py (sibling module); these
tests verify the chat path wiring: DPoP headers on the outbound POST, 401
session rebuild, and token-endpoint failure semantics. No real network: the
DPoP manager factory, the proxy runtime and the HTTP session are all mocked.
"""

import json
import time
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.control.proxy.models import ProxyFeedbackKind
from app.dataplane.reverse.protocol import xai_console_chat as chat_module
from app.dataplane.reverse.protocol.dpop import (
    DPoPError,
    DPoPSession,
    DPoPSessionManager,
    DPoPTokenEndpointError,
    dpop_jwk_thumbprint,
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


class _LeaseProfile:
    """Per-lease clearance profile so tests can vary cf_clearance per lease."""

    def __init__(self, cf_clearance: str = "") -> None:
        self.user_agent = CONSOLE_UA
        self.browser = "chrome120"
        self.cf_cookies = ""
        self.cf_clearance = cf_clearance


@pytest.fixture(autouse=True)
def _reset_chat_dpop_globals():
    """The chat path caches one manager per SSO token at module scope."""
    chat_module._dpop_manager = None
    chat_module._dpop_manager_token = None
    yield
    chat_module._dpop_manager = None
    chat_module._dpop_manager_token = None


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


def _b64url(data: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


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

    with _patched(manager, http) as (_proxy, lease):
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
    assert (
        headers["x-cluster"] == "https://us-east-1.api.x.ai"
    )  # G6-M2: /responses only
    manager.get_or_fetch.assert_awaited_once_with(CONSOLE_BASE, 0, 0, "sso-tok", lease)


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


# ---------------------------------------------------------------------------
# G6-I1: definitive-block predicate wired into the chat-path manager
# ---------------------------------------------------------------------------


def _build_chat_manager(token: str):
    """Real ``_get_dpop_manager`` (not patched); caller patches the exchange."""
    return chat_module._get_dpop_manager(token)


@pytest.mark.asyncio
async def test_chat_manager_definitive_block_403_keeps_clearance():
    """G6-I1: a blocked-user 403 on /dpop/token must NOT invalidate clearance.

    Mirrors Go fetchDPoPSession: only 403s that are *not* a definitive account
    block call lease.InvalidateClearance().
    """
    with (
        patch.object(
            chat_module,
            "_post_dpop_token",
            AsyncMock(return_value=(403, {"raw_body": '{"error": "user is blocked"}'})),
        ),
        patch(
            "app.dataplane.proxy.adapters.headers._resolve_profile",
            return_value=_Profile(),
        ),
    ):
        manager = _build_chat_manager("sso-tok")
        with pytest.raises(DPoPTokenEndpointError) as exc_info:
            await manager.get_or_fetch(CONSOLE_BASE, 0, 0, "sso-tok")

    assert exc_info.value.status == 403
    assert exc_info.value.invalidate_clearance is False


@pytest.mark.asyncio
async def test_chat_manager_non_definitive_403_invalidates_clearance():
    """G6-I1: a plain (CF/other) 403 still invalidates clearance."""
    with (
        patch.object(
            chat_module,
            "_post_dpop_token",
            AsyncMock(return_value=(403, {"raw_body": '{"error": "cf-challenge"}'})),
        ),
        patch(
            "app.dataplane.proxy.adapters.headers._resolve_profile",
            return_value=_Profile(),
        ),
    ):
        manager = _build_chat_manager("sso-tok")
        with pytest.raises(DPoPTokenEndpointError) as exc_info:
            await manager.get_or_fetch(CONSOLE_BASE, 0, 0, "sso-tok")

    assert exc_info.value.status == 403
    assert exc_info.value.invalidate_clearance is True


# ---------------------------------------------------------------------------
# G6-I2: token-exchange browser headers follow the current lease
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manager_browser_headers_callable_rebuilt_per_fetch():
    """dpop.py contract: a callable browser_headers is resolved per exchange."""
    box = ["c1"]
    captured: list[dict[str, str]] = []

    async def post_json(
        _url: str,
        headers: dict[str, str],
        _payload: dict[str, Any],
        _lease: Any | None = None,
    ) -> tuple[int, dict[str, Any]]:
        captured.append(headers)
        raise DPoPTokenEndpointError(403, b"{}", invalidate_clearance=False)

    manager = DPoPSessionManager(
        post_json, browser_headers=lambda _lease: {"Cookie": f"cf_clearance={box[0]}"}
    )
    with pytest.raises(DPoPTokenEndpointError):
        await manager.get_or_fetch(CONSOLE_BASE, 0, 0, "tok")
    box[0] = "c2"
    with pytest.raises(DPoPTokenEndpointError):
        await manager.get_or_fetch(CONSOLE_BASE, 0, 0, "tok-other")

    assert captured[0]["Cookie"] == "cf_clearance=c1"
    assert captured[1]["Cookie"] == "cf_clearance=c2"
    assert captured[0]["Content-Type"] == "application/json"


@pytest.mark.asyncio
async def test_chat_exchange_headers_follow_current_lease():
    """G6-I2: a re-exchange after the lease changed carries the new cf_clearance.

    The manager is cached per SSO token, but the token-exchange headers must be
    derived from the *current* lease (Go applies per-request browser headers).
    """
    captured: list[dict[str, str]] = []

    async def fake_post(
        _url: str,
        headers: dict[str, str],
        _payload: dict[str, Any],
        _lease: Any | None = None,
    ) -> tuple[int, dict[str, Any]]:
        captured.append(headers)
        raise DPoPTokenEndpointError(403, b"{}", invalidate_clearance=False)

    lease1 = MagicMock(proxy_url=None, cf_clearance="c1")
    lease2 = MagicMock(proxy_url=None, cf_clearance="c2")
    with (
        patch.object(chat_module, "_post_dpop_token", fake_post),
        patch(
            "app.dataplane.proxy.adapters.headers._resolve_profile",
            side_effect=lambda lease: _LeaseProfile(cf_clearance=lease.cf_clearance),
        ),
    ):
        manager = chat_module._get_dpop_manager("sso-tok")
        with pytest.raises(DPoPTokenEndpointError):
            await manager.get_or_fetch(CONSOLE_BASE, 0, 0, "sso-tok", lease1)

        # Same token → same manager, but the lease moved on.
        manager2 = chat_module._get_dpop_manager("sso-tok")
        assert manager2 is manager
        with pytest.raises(DPoPTokenEndpointError):
            await manager.get_or_fetch(CONSOLE_BASE, 0, 0, "sso-tok", lease2)

    assert "cf_clearance=c1" in captured[0]["Cookie"]
    assert "cf_clearance=c2" in captured[1]["Cookie"]


# ---------------------------------------------------------------------------
# G6-M2: x-cluster only on /responses (never on the /dpop/token exchange)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_dpop_exchange_has_no_x_cluster():
    """G6-M2: the token exchange must not carry x-cluster (Go doDPoPRequest)."""
    captured: list[dict[str, str]] = []

    async def fake_post(
        _url: str,
        headers: dict[str, str],
        _payload: dict[str, Any],
        _lease: Any | None = None,
    ) -> tuple[int, dict[str, Any]]:
        captured.append(headers)
        raise DPoPTokenEndpointError(403, b"{}", invalidate_clearance=False)

    with (
        patch.object(chat_module, "_post_dpop_token", fake_post),
        patch(
            "app.dataplane.proxy.adapters.headers._resolve_profile",
            return_value=_Profile(),
        ),
    ):
        manager = chat_module._get_dpop_manager("sso-tok")
        with pytest.raises(DPoPTokenEndpointError):
            await manager.get_or_fetch(CONSOLE_BASE, 0, 0, "sso-tok")

    assert "x-cluster" not in captured[0]


# ---------------------------------------------------------------------------
# Fix A regression: the token exchange must reuse the chat request's lease
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_exchange_reuses_chat_lease_no_second_acquire():
    """Before the fix, _post_dpop_token called proxy.acquire() itself while the
    request headers (Cookie: cf_clearance) came from the global
    _dpop_manager_lease — transport egress node != clearance node, so
    Cloudflare served a challenge interstitial and the exchange failed
    (Go fetchDPoPSession always reuses the caller's lease)."""
    proxy = AsyncMock()
    lease, other_lease = MagicMock(), MagicMock()
    proxy.acquire.side_effect = [lease, other_lease]

    session_kwarg_leases: list[Any] = []

    def _record_session_kwargs(*, lease=None, **kwargs):
        session_kwarg_leases.append(lease)
        return {}

    def _token_or_sse(url, headers=None, data=None, timeout=None, **kwargs):
        if url == CONSOLE_RESPONSES:
            return _response(sse_lines=_sse_ok_lines())
        # Mint a DPoP access token bound to the JWK the exchange just posted.
        jwk = json.loads(data or b"")["jwk"]
        now = int(time.time())
        token = (
            f"{_b64url(json.dumps({'alg': 'ES256', 'typ': 'JWT'}).encode())}."
            f"{_b64url(json.dumps({'exp': now + 3600, 'cnf': {'jkt': dpop_jwk_thumbprint(jwk)}}).encode())}."
            f"{_b64url(b'sig')}"
        )
        return _response(
            200,
            body=json.dumps(
                {"access_token": token, "token_type": "DPoP", "expires_in": 3600}
            ),
        )

    http = _session_mock()
    http.post.side_effect = _token_or_sse

    with (
        patch("app.dataplane.proxy.get_proxy_runtime", return_value=proxy),
        patch(
            "app.dataplane.proxy.adapters.session.ResettableSession",
            return_value=http,
        ),
        patch(
            "app.dataplane.proxy.adapters.session.build_session_kwargs",
            side_effect=_record_session_kwargs,
        ),
        patch(
            "app.dataplane.proxy.adapters.headers._resolve_profile",
            return_value=_Profile(),
        ),
    ):
        events = [
            ev
            async for ev in stream_console_chat(
                "sso-tok", {"model": "grok-4.3", "input": []}
            )
        ]

    # The token exchange must NOT acquire its own lease.
    assert proxy.acquire.await_count == 1
    # Same egress node, same cf_clearance cookie for exchange and chat POST.
    assert session_kwarg_leases == [lease, lease]
    assert all(l is lease for l in session_kwarg_leases)
    assert events == [
        ("response.output_text.delta", '{"delta": "hi"}'),
        ("response.completed", '{"response": {"usage": {"total_tokens": 3}}}'),
    ]
