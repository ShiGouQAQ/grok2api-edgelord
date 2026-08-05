"""Tests for app/dataplane/reverse/protocol/xai_console_usage.py.

Covers the Go→Python port of chenyme/grok2api PR #853 console/quota.go:
payload validation (all three kinds, value sanity), window construction
(24h predicted recovery when chat is exhausted), and the error taxonomy
(401 / definitive 403 → credential_rejected; other 403 → clearance-required;
transport → 502; malformed payload → ConsoleQuotaError). No real network.
"""

import json
import time
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest

from app.control.account.enums import QuotaSource
from app.control.proxy.models import ProxyFeedbackKind
from app.dataplane.reverse.protocol.dpop import (
    DPoPTokenEndpointError,
    dpop_jwk_thumbprint,
)
from app.dataplane.reverse.protocol.xai_console_usage import (
    CONSOLE_PREDICTED_CHAT_RECOVERY_WINDOW_S,
    ConsoleClearanceRequiredError,
    ConsoleQuotaError,
    ConsoleUsageResult,
    fetch_console_usage,
    parse_console_usage_payload,
)
from app.dataplane.reverse.runtime.endpoint_table import CONSOLE_BASE
from app.platform.errors import UpstreamError


def _usage_payload(*, chat_remaining=10, chat_used=10, chat_limit=20):
    return {
        "quotas": [
            {
                "kind": "chat",
                "limit": chat_limit,
                "used": chat_used,
                "remaining": chat_remaining,
                "last_consumed_at": 0,
            },
            {
                "kind": "image",
                "limit": 100,
                "used": 50,
                "remaining": 50,
                "last_consumed_at": 0,
            },
            {
                "kind": "video",
                "limit": 10,
                "used": 7,
                "remaining": 3,
                "last_consumed_at": 0,
            },
        ]
    }


class _FakeResponse:
    def __init__(self, status_code: int, content: bytes | str) -> None:
        self.status_code = status_code
        self.content = content if isinstance(content, bytes) else content.encode()


def _b64url(data: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


async def _fake_dpop_token_post(payload_jwk):
    """Build a token-endpoint reply whose cnf.jkt matches the posted JWK."""

    def side_effect(endpoint, headers=None, data=None, timeout=None, **kwargs):
        assert data is not None
        jwk = orjson.loads(data)["jwk"]
        jkt = dpop_jwk_thumbprint(jwk)
        now = int(time.time())
        token = (
            f"{_b64url(json.dumps({'alg': 'ES256', 'typ': 'JWT'}).encode())}."
            f"{_b64url(json.dumps({'exp': now + 3600, 'cnf': {'jkt': jkt}}).encode())}."
            f"{_b64url(b'sig')}"
        )
        return _FakeResponse(
            200,
            orjson.dumps(
                {"access_token": token, "token_type": "DPoP", "expires_in": 3600}
            ),
        )

    return side_effect


def _patch_runtime():
    mock_proxy = AsyncMock()
    mock_lease = MagicMock()
    mock_proxy.acquire.return_value = mock_lease
    return mock_proxy, mock_lease


def _patch_fetch_basics(mock_proxy):
    return [
        patch("app.dataplane.proxy.get_proxy_runtime", return_value=mock_proxy),
        patch(
            "app.dataplane.reverse.protocol.xai_console_usage.build_session_kwargs",
            return_value={},
        ),
    ]


# ── Payload parsing (unit) ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parse_payload_all_kinds_builds_windows():
    result = parse_console_usage_payload(_usage_payload(), now_ms=1_000_000)

    assert isinstance(result, ConsoleUsageResult)
    assert result.chat.remaining == 10
    assert result.chat.total == 20
    assert result.chat.window_seconds == CONSOLE_PREDICTED_CHAT_RECOVERY_WINDOW_S
    assert result.chat.reset_at is None
    assert result.chat.synced_at == 1_000_000
    assert result.chat.source == QuotaSource.REAL
    assert result.image.window_seconds == 0
    assert result.image.reset_at is None
    assert result.image.source == QuotaSource.REAL
    assert result.video.remaining == 3
    assert result.video.total == 10
    assert result.video.window_seconds == 0
    assert result.used == {"chat": 10, "image": 50, "video": 7}


@pytest.mark.asyncio
async def test_parse_payload_chat_exhausted_sets_24h_reset():
    now_ms = 1_750_000_000_000
    result = parse_console_usage_payload(
        _usage_payload(chat_remaining=0, chat_used=20), now_ms=now_ms
    )

    assert result.chat.remaining == 0
    assert (
        result.chat.reset_at == now_ms + CONSOLE_PREDICTED_CHAT_RECOVERY_WINDOW_S * 1000
    )


@pytest.mark.asyncio
async def test_parse_payload_missing_kind_raises():
    payload = _usage_payload()
    payload["quotas"] = payload["quotas"][:2]  # drop video

    with pytest.raises(ConsoleQuotaError, match="video"):
        parse_console_usage_payload(payload, now_ms=1)


@pytest.mark.asyncio
async def test_parse_payload_remaining_over_limit_raises():
    payload = _usage_payload(chat_remaining=21, chat_limit=20)

    with pytest.raises(ConsoleQuotaError):
        parse_console_usage_payload(payload, now_ms=1)


@pytest.mark.asyncio
async def test_parse_payload_negative_values_raise():
    payload = _usage_payload(chat_limit=-1)

    with pytest.raises(ConsoleQuotaError):
        parse_console_usage_payload(payload, now_ms=1)


@pytest.mark.asyncio
async def test_parse_payload_missing_quotas_list_raises():
    with pytest.raises(ConsoleQuotaError, match="quotas"):
        parse_console_usage_payload({}, now_ms=1)


# ── Full wiring (real DPoP exchange, mocked HTTP) ────────────────────────


@pytest.mark.asyncio
async def test_fetch_success_full_dpop_wiring():
    mock_proxy, _mock_lease = _patch_runtime()

    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    session_mock.post.side_effect = await _fake_dpop_token_post(None)
    session_mock.get.return_value = _FakeResponse(200, orjson.dumps(_usage_payload()))
    session_cls = MagicMock(return_value=session_mock)

    with (
        patch("app.dataplane.proxy.get_proxy_runtime", return_value=mock_proxy),
        patch(
            "app.dataplane.reverse.protocol.xai_console_usage.ResettableSession",
            session_cls,
        ),
        patch(
            "app.dataplane.reverse.protocol.xai_console_usage.build_session_kwargs",
            return_value={},
        ),
        patch(
            "app.dataplane.reverse.protocol.xai_console_usage.build_console_headers",
            return_value={},
        ),
    ):
        result = await fetch_console_usage("sso-test-token")

    assert result.chat.remaining == 10
    assert result.chat.total == 20
    assert result.chat.source == QuotaSource.REAL
    assert result.used == {"chat": 10, "image": 50, "video": 7}
    mock_proxy.acquire.assert_called_once_with(clearance_origin=CONSOLE_BASE)
    mock_proxy.feedback.assert_called_once()
    assert mock_proxy.feedback.call_args[0][1].kind == ProxyFeedbackKind.SUCCESS
    # The DPoP GET must carry a signed proof + DPoP access token.
    get_headers = session_mock.get.call_args.kwargs["headers"]
    assert get_headers["Authorization"].startswith("DPoP ")
    assert get_headers["DPoP"]


# ── Error taxonomy (do_dpop_request mocked at the seam) ──────────────────


@pytest.mark.asyncio
async def test_fetch_401_credential_rejected():
    mock_proxy, _ = _patch_runtime()
    with ExitStack() as _stack:
        for _p in _patch_fetch_basics(mock_proxy):
            _stack.enter_context(_p)
        _stack.enter_context(
            patch(
                "app.dataplane.reverse.protocol.xai_console_usage.do_dpop_request",
                AsyncMock(
                    return_value=(401, b'{"error": {"code": "unauthorized"}}', {})
                ),
            )
        )

        with pytest.raises(UpstreamError) as exc_info:
            await fetch_console_usage("sso-test-token")

    assert exc_info.value.status == 401
    assert exc_info.value.credential_rejected is True
    mock_proxy.feedback.assert_called_once()
    assert mock_proxy.feedback.call_args[0][1].kind == ProxyFeedbackKind.FORBIDDEN


@pytest.mark.asyncio
async def test_fetch_403_definitive_block_credential_rejected():
    mock_proxy, _ = _patch_runtime()
    body = b'{"error":"User is blocked [WKE=unauthorized:blocked-user]"}'
    with ExitStack() as _stack:
        for _p in _patch_fetch_basics(mock_proxy):
            _stack.enter_context(_p)
        _stack.enter_context(
            patch(
                "app.dataplane.reverse.protocol.xai_console_usage.do_dpop_request",
                AsyncMock(return_value=(403, body, {})),
            )
        )

        with pytest.raises(UpstreamError) as exc_info:
            await fetch_console_usage("sso-test-token")

    assert exc_info.value.status == 403
    assert exc_info.value.credential_rejected is True
    assert not isinstance(exc_info.value, ConsoleClearanceRequiredError)


@pytest.mark.asyncio
async def test_fetch_403_non_definitive_clearance_required():
    mock_proxy, _ = _patch_runtime()
    with ExitStack() as _stack:
        for _p in _patch_fetch_basics(mock_proxy):
            _stack.enter_context(_p)
        _stack.enter_context(
            patch(
                "app.dataplane.reverse.protocol.xai_console_usage.do_dpop_request",
                AsyncMock(
                    return_value=(403, b'{"error":{"code":"permission-denied"}}', {})
                ),
            )
        )

        with pytest.raises(ConsoleClearanceRequiredError) as exc_info:
            await fetch_console_usage("sso-test-token")

    assert exc_info.value.status == 403
    assert exc_info.value.credential_rejected is False
    assert exc_info.value.invalidate_clearance is True
    mock_proxy.feedback.assert_called_once()
    assert mock_proxy.feedback.call_args[0][1].kind == ProxyFeedbackKind.FORBIDDEN


@pytest.mark.asyncio
async def test_fetch_transport_error_502():
    mock_proxy, _ = _patch_runtime()
    with ExitStack() as _stack:
        for _p in _patch_fetch_basics(mock_proxy):
            _stack.enter_context(_p)
        _stack.enter_context(
            patch(
                "app.dataplane.reverse.protocol.xai_console_usage.do_dpop_request",
                AsyncMock(
                    side_effect=UpstreamError(
                        "Transport request failed: boom", status=502
                    )
                ),
            )
        )

        with pytest.raises(UpstreamError) as exc_info:
            await fetch_console_usage("sso-test-token")

    assert exc_info.value.status == 502
    mock_proxy.feedback.assert_called_once()
    assert mock_proxy.feedback.call_args[0][1].kind == ProxyFeedbackKind.TRANSPORT_ERROR


@pytest.mark.asyncio
async def test_fetch_dpop_token_endpoint_403_propagates_clearance():
    mock_proxy, _ = _patch_runtime()
    with ExitStack() as _stack:
        for _p in _patch_fetch_basics(mock_proxy):
            _stack.enter_context(_p)
        _stack.enter_context(
            patch(
                "app.dataplane.reverse.protocol.xai_console_usage.do_dpop_request",
                AsyncMock(
                    side_effect=DPoPTokenEndpointError(
                        403, b'{"error":"cf blocked"}', invalidate_clearance=True
                    )
                ),
            )
        )

        with pytest.raises(ConsoleClearanceRequiredError) as exc_info:
            await fetch_console_usage("sso-test-token")

    assert exc_info.value.status == 403
    assert exc_info.value.invalidate_clearance is True
    assert exc_info.value.credential_rejected is False


@pytest.mark.asyncio
async def test_fetch_dpop_token_endpoint_401_propagates_credential():
    mock_proxy, _ = _patch_runtime()
    with ExitStack() as _stack:
        for _p in _patch_fetch_basics(mock_proxy):
            _stack.enter_context(_p)
        _stack.enter_context(
            patch(
                "app.dataplane.reverse.protocol.xai_console_usage.do_dpop_request",
                AsyncMock(
                    side_effect=DPoPTokenEndpointError(401, b'{"error":"bad token"}')
                ),
            )
        )

        with pytest.raises(UpstreamError) as exc_info:
            await fetch_console_usage("sso-test-token")

    assert exc_info.value.status == 401
    assert exc_info.value.credential_rejected is True


@pytest.mark.asyncio
async def test_fetch_malformed_payload_console_quota_error():
    mock_proxy, _ = _patch_runtime()
    with ExitStack() as _stack:
        for _p in _patch_fetch_basics(mock_proxy):
            _stack.enter_context(_p)
        _stack.enter_context(
            patch(
                "app.dataplane.reverse.protocol.xai_console_usage.do_dpop_request",
                AsyncMock(return_value=(200, orjson.dumps({"quotas": []}), {})),
            )
        )

        with pytest.raises(ConsoleQuotaError):
            await fetch_console_usage("sso-test-token")

    # 2xx → success feedback happens before payload validation fails.
    mock_proxy.feedback.assert_called_once()
    assert mock_proxy.feedback.call_args[0][1].kind == ProxyFeedbackKind.SUCCESS


@pytest.mark.asyncio
async def test_fetch_429_rate_limited_feedback():
    mock_proxy, _ = _patch_runtime()
    with ExitStack() as _stack:
        for _p in _patch_fetch_basics(mock_proxy):
            _stack.enter_context(_p)
        _stack.enter_context(
            patch(
                "app.dataplane.reverse.protocol.xai_console_usage.do_dpop_request",
                AsyncMock(return_value=(429, b'{"error":"rate limited"}', {})),
            )
        )

        with pytest.raises(UpstreamError) as exc_info:
            await fetch_console_usage("sso-test-token")

    assert exc_info.value.status == 429
    mock_proxy.feedback.assert_called_once()
    assert mock_proxy.feedback.call_args[0][1].kind == ProxyFeedbackKind.RATE_LIMITED
