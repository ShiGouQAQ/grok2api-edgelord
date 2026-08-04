"""Tests for SSO→Build credential conversion.

Tests cover both PKCE-CS path (preferred) and Device Flow path (fallback),
plus all utility functions ported from Go sso_build.go.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.control.account.sso_build import (
    BuildCredentialSeed,
    convert_sso_to_build,
    decode_build_claims,
    normalize_sso_token,
    safe_xai_url,
)


# ═══════════════════════════════════════════════════════════════════════════
# Utility function tests
# ═══════════════════════════════════════════════════════════════════════════


class TestNormalizeSSOToken:
    def test_strips_sso_prefix(self) -> None:
        assert normalize_sso_token("sso=abc123") == "abc123"

    def test_strips_sso_prefix_case_insensitive(self) -> None:
        assert normalize_sso_token("SSO=abc123") == "abc123"

    def test_chops_at_semicolon(self) -> None:
        assert normalize_sso_token("sso=abc123;other=stuff") == "abc123"

    def test_removes_control_chars(self) -> None:
        assert normalize_sso_token("abc\r\n\x00123") == "abc123"

    def test_trims_whitespace(self) -> None:
        assert normalize_sso_token("  abc123  ") == "abc123"

    def test_empty_string(self) -> None:
        assert normalize_sso_token("") == ""

    def test_sso_prefix_and_semicolon(self) -> None:
        assert normalize_sso_token("  SSO=  mytoken  ; extra=1  ") == "mytoken"


class TestSafeXAIURL:
    def test_valid_x_ai(self) -> None:
        assert safe_xai_url("https://x.ai/") is True

    def test_valid_subdomain(self) -> None:
        assert safe_xai_url("https://auth.x.ai/oauth2/token") is True

    def test_valid_deep_subdomain(self) -> None:
        assert safe_xai_url("https://api.accounts.x.ai/v1") is True

    def test_http_rejected(self) -> None:
        assert safe_xai_url("http://x.ai/") is False

    def test_wrong_domain(self) -> None:
        assert safe_xai_url("https://evil.com/") is False

    def test_with_credentials(self) -> None:
        assert safe_xai_url("https://user:pass@x.ai/") is False

    def test_empty_string(self) -> None:
        assert safe_xai_url("") is False

    def test_invalid_url(self) -> None:
        assert safe_xai_url("not a url") is False

    def test_tricky_subdomain(self) -> None:
        assert safe_xai_url("https://x.ai.evil.com/") is False

    def test_missing_hostname(self) -> None:
        assert safe_xai_url("https:///path") is False


class TestDecodeBuildClaims:
    def test_valid_jwt(self) -> None:
        claims = {"sub": "user123", "email": "test@x.ai", "team_id": "team456"}
        payload = base64url(json.dumps(claims))
        token = f"header.{payload}.signature"
        result = decode_build_claims(token)
        assert result is not None
        assert result["sub"] == "user123"
        assert result["email"] == "test@x.ai"
        assert result["team_id"] == "team456"

    def test_missing_parts(self) -> None:
        assert decode_build_claims("onlyonepart") is None

    def test_invalid_base64(self) -> None:
        assert decode_build_claims("header.!!!invalid!!!.sig") is None

    def test_empty_string(self) -> None:
        assert decode_build_claims("") is None

    def test_single_dot_no_payload(self) -> None:
        result = decode_build_claims("header.")
        assert result is None or result == {}


def base64url(data: str) -> str:
    """Helper: base64url-encode a JSON string (no padding)."""
    import base64 as _b64

    return _b64.urlsafe_b64encode(data.encode()).rstrip(b"=").decode()


# ═══════════════════════════════════════════════════════════════════════════
# PKCE-CS path tests
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_pkce_cs_success() -> None:
    """PKCE-CS path succeeds → returns BuildCredentialSeed with valid tokens."""
    with patch(
        "app.control.account.sso_build._mint_via_pkce_cs",
        new_callable=AsyncMock,
    ) as mock_pkce:
        mock_pkce.return_value = BuildCredentialSeed(
            access_token="at-pkce",
            refresh_token="rt-pkce",
            id_token="id-pkce",
            expires_in=21600,
            name="test@x.ai",
            email="test@x.ai",
            user_id="user123",
            team_id="team456",
            source_key="sso-build:abc123",
        )

        result = await convert_sso_to_build("good-sso-token")

    assert result.access_token == "at-pkce"
    assert result.refresh_token == "rt-pkce"
    assert result.email == "test@x.ai"
    assert result.user_id == "user123"
    assert result.team_id == "team456"
    assert result.expires_in == 21600
    mock_pkce.assert_called_once()


@pytest.mark.asyncio
async def test_pkce_cs_fallback_to_device_flow() -> None:
    """PKCE-CS fails → falls back to Device Flow → returns Device Flow result."""
    with (
        patch(
            "app.control.account.sso_build._mint_via_pkce_cs",
            new_callable=AsyncMock,
            side_effect=RuntimeError("PKCE-CS: gRPC call failed"),
        ),
        patch(
            "app.control.account.sso_build._mint_via_device_flow",
            new_callable=AsyncMock,
        ) as mock_device,
    ):
        mock_device.return_value = BuildCredentialSeed(
            access_token="at-device",
            refresh_token="rt-device",
            expires_in=7200,
        )

        result = await convert_sso_to_build("sso-token")

    assert result.access_token == "at-device"
    assert result.expires_in == 7200
    mock_device.assert_called_once()


@pytest.mark.asyncio
async def test_pkce_cs_failure_no_fallback_when_device_also_fails() -> None:
    """Both PKCE-CS and Device Flow fail → propagate Device Flow error."""
    with (
        patch(
            "app.control.account.sso_build._mint_via_pkce_cs",
            new_callable=AsyncMock,
            side_effect=RuntimeError("PKCE-CS down"),
        ),
        patch(
            "app.control.account.sso_build._mint_via_device_flow",
            new_callable=AsyncMock,
            side_effect=TimeoutError("Device Flow timed out"),
        ),
    ):
        with pytest.raises(TimeoutError, match="timed out"):
            await convert_sso_to_build("sso-token")


# ═══════════════════════════════════════════════════════════════════════════
# Device Flow path tests
# ═══════════════════════════════════════════════════════════════════════════


def _make_ctx_resp(
    data: dict[str, Any] | None = None,
    status: int = 200,
    text_data: str | None = None,
) -> MagicMock:
    """Create a mock aiohttp response with async context manager."""
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=data or {})
    resp.text = AsyncMock(return_value=text_data or str(data or {}))
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_url_resp(
    status: int = 200,
    url: str = "",
) -> MagicMock:
    """Create a mock response with a specific final URL."""
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value={})
    resp.text = AsyncMock(return_value="")
    mock_url = MagicMock()
    mock_url.__str__ = MagicMock(return_value=url)
    resp.url = mock_url
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_session(responses: list[MagicMock]) -> MagicMock:
    """Create a mock aiohttp ClientSession with ordered response queue."""
    from collections import deque

    queue = deque(responses)

    def _next(*args: Any, **kwargs: Any) -> MagicMock:
        if not queue:
            raise RuntimeError(f"no more mock responses (consumed={len(responses)})")
        return queue.popleft()

    session = MagicMock()
    session.post = MagicMock(side_effect=_next)
    session.get = MagicMock(side_effect=_next)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


@pytest.fixture
def _patch_pkce_as_unavailable() -> Any:
    """Patch PKCE-CS to be unavailable so Device Flow runs."""
    return patch(
        "app.control.account.sso_build._mint_via_pkce_cs",
        new_callable=AsyncMock,
        side_effect=RuntimeError("PKCE-CS unavailable"),
    )


@pytest.fixture
def _patch_sleep() -> Any:
    """Patch asyncio.sleep to avoid actual waiting."""
    return patch("app.control.account.sso_build.asyncio.sleep", new_callable=AsyncMock)


def _patch_monotonic_with_advance(start: float = 0.0, delta: float = 5.0) -> Any:
    """Create a patch for time.monotonic that advances by *delta* per call."""
    import time as _tm

    state: list[float] = [start]

    def _monotonic() -> float:
        val = state[0]
        state[0] += delta
        return val

    return patch.object(_tm, "monotonic", side_effect=_monotonic)


@pytest.mark.asyncio
async def test_device_flow_success(
    _patch_pkce_as_unavailable: Any, _patch_sleep: Any
) -> None:
    """Device Flow path: full success → returns BuildCredentialSeed."""
    device_resp = _make_ctx_resp(
        {
            "device_code": "dc-123",
            "user_code": "UC-ABC",
            "verification_uri_complete": "https://auth.x.ai/activate?user_code=UC-ABC",
            "interval": 5,
            "expires_in": 1800,
        }
    )
    pre_resp = _make_url_resp(status=200, url="https://accounts.x.ai/")
    verify_uri_resp = _make_url_resp(status=200)
    verify_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/consent")
    approve_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/done")
    token_resp = _make_ctx_resp(
        {
            "access_token": "at-device",
            "refresh_token": "rt-device",
            "id_token": "",
            "expires_in": 7200,
        }
    )

    responses = [
        pre_resp,  # 1. GET accounts.x.ai/
        device_resp,  # 2. POST /device/code
        verify_uri_resp,  # 3. GET verification_uri_complete
        verify_resp,  # 4. POST /verify → consent URL
        approve_resp,  # 5. POST /approve → done URL
        token_resp,  # 6. POST /token → success
    ]

    with (
        patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
        _patch_pkce_as_unavailable,
        _patch_sleep,
    ):
        mock_cls.return_value = _make_session(responses)
        result = await convert_sso_to_build("good-sso")

    assert result.access_token == "at-device"
    assert result.refresh_token == "rt-device"
    assert result.expires_in == 7200


@pytest.mark.asyncio
async def test_device_flow_invalid_sso(
    _patch_pkce_as_unavailable: Any, _patch_sleep: Any
) -> None:
    """Invalid SSO token → PermissionError from pre-validation."""
    pre_resp = _make_url_resp(status=200, url="https://accounts.x.ai/sign-in")

    with (
        patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
        _patch_pkce_as_unavailable,
        _patch_sleep,
    ):
        mock_cls.return_value = _make_session([pre_resp])
        with pytest.raises(PermissionError, match="invalid or expired"):
            await convert_sso_to_build("invalid-token")


@pytest.mark.asyncio
async def test_device_flow_unauthorized_status(
    _patch_pkce_as_unavailable: Any, _patch_sleep: Any
) -> None:
    """Pre-validation returns 401 → PermissionError."""
    pre_resp = _make_url_resp(status=401, url="https://accounts.x.ai/login")

    with (
        patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
        _patch_pkce_as_unavailable,
        _patch_sleep,
    ):
        mock_cls.return_value = _make_session([pre_resp])
        with pytest.raises(PermissionError, match="invalid or expired"):
            await convert_sso_to_build("expired-token")


@pytest.mark.asyncio
async def test_device_flow_verify_no_consent(
    _patch_pkce_as_unavailable: Any, _patch_sleep: Any
) -> None:
    """Verify response lacks 'consent' in URL → RuntimeError."""
    device_resp = _make_ctx_resp(
        {
            "device_code": "dc-123",
            "user_code": "UC-ABC",
            "verification_uri_complete": "https://auth.x.ai/activate?user_code=UC-ABC",
            "interval": 5,
            "expires_in": 1800,
        }
    )
    pre_resp = _make_url_resp(status=200, url="https://accounts.x.ai/")
    verify_uri_resp = _make_url_resp(status=200)
    verify_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/denied")

    responses = [pre_resp, device_resp, verify_uri_resp, verify_resp]

    with (
        patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
        _patch_pkce_as_unavailable,
        _patch_sleep,
    ):
        mock_cls.return_value = _make_session(responses)
        with pytest.raises(RuntimeError, match="no.*consent"):
            await convert_sso_to_build("sso-token")


@pytest.mark.asyncio
async def test_device_flow_approve_no_done(
    _patch_pkce_as_unavailable: Any, _patch_sleep: Any
) -> None:
    """Approve response lacks 'done' in URL → RuntimeError."""
    device_resp = _make_ctx_resp(
        {
            "device_code": "dc-123",
            "user_code": "UC-ABC",
            "verification_uri_complete": "https://auth.x.ai/activate?user_code=UC-ABC",
            "interval": 5,
            "expires_in": 1800,
        }
    )
    pre_resp = _make_url_resp(status=200, url="https://accounts.x.ai/")
    verify_uri_resp = _make_url_resp(status=200)
    verify_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/consent")
    approve_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/finished")

    responses = [pre_resp, device_resp, verify_uri_resp, verify_resp, approve_resp]

    with (
        patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
        _patch_pkce_as_unavailable,
        _patch_sleep,
    ):
        mock_cls.return_value = _make_session(responses)
        with pytest.raises(RuntimeError, match="no.*done"):
            await convert_sso_to_build("sso-token")


@pytest.mark.asyncio
async def test_device_flow_approve_fails(
    _patch_pkce_as_unavailable: Any, _patch_sleep: Any
) -> None:
    """Device Flow approve returns 400 → RuntimeError."""
    device_resp = _make_ctx_resp(
        {
            "device_code": "dc-123",
            "user_code": "UC-ABC",
            "verification_uri_complete": "https://auth.x.ai/activate?user_code=UC-ABC",
            "interval": 5,
            "expires_in": 1800,
        }
    )
    pre_resp = _make_url_resp(status=200, url="https://accounts.x.ai/")
    verify_uri_resp = _make_url_resp(status=200)
    verify_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/consent")
    approve_resp = _make_url_resp(status=400, url="https://auth.x.ai/device/error")

    responses = [pre_resp, device_resp, verify_uri_resp, verify_resp, approve_resp]

    with (
        patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
        _patch_pkce_as_unavailable,
        _patch_sleep,
    ):
        mock_cls.return_value = _make_session(responses)
        with pytest.raises(RuntimeError, match="Device approve failed"):
            await convert_sso_to_build("sso-token")


@pytest.mark.asyncio
async def test_device_flow_timeout(
    _patch_pkce_as_unavailable: Any, _patch_sleep: Any
) -> None:
    """Device Flow polling times out → TimeoutError."""
    device_resp = _make_ctx_resp(
        {
            "device_code": "dc-123",
            "user_code": "UC-ABC",
            "verification_uri_complete": "https://auth.x.ai/activate?user_code=UC-ABC",
            "interval": 5,
            "expires_in": 1800,
        }
    )
    pre_resp = _make_url_resp(status=200, url="https://accounts.x.ai/")
    verify_uri_resp = _make_url_resp(status=200)
    verify_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/consent")
    approve_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/done")
    pending_resp = _make_ctx_resp({"error": "authorization_pending"}, status=400)

    # Each time.monotonic() call advances by 5s. With expires_in=1800,
    # deadline = min(1800, 75) = 75s. After 15 iterations time advances
    # 75s → loop times out.
    responses = [
        pre_resp,
        device_resp,
        verify_uri_resp,
        verify_resp,
        approve_resp,
    ] + [pending_resp] * 15

    with (
        patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
        _patch_pkce_as_unavailable,
        _patch_sleep,
        _patch_monotonic_with_advance(start=0.0, delta=5.0),
    ):
        mock_cls.return_value = _make_session(responses)
        with pytest.raises(TimeoutError, match="timed out"):
            await convert_sso_to_build("sso-token")


@pytest.mark.asyncio
async def test_device_flow_slow_down_backoff(
    _patch_pkce_as_unavailable: Any, _patch_sleep: Any
) -> None:
    """Device Flow slow_down increases interval by 5s (not 2s) each time."""
    device_resp = _make_ctx_resp(
        {
            "device_code": "dc-123",
            "user_code": "UC-ABC",
            "verification_uri_complete": "https://auth.x.ai/activate?user_code=UC-ABC",
            "interval": 5,
            "expires_in": 1800,
        }
    )
    pre_resp = _make_url_resp(status=200, url="https://accounts.x.ai/")
    verify_uri_resp = _make_url_resp(status=200)
    verify_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/consent")
    approve_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/done")
    slow_down_resp = _make_ctx_resp({"error": "slow_down"}, status=400)

    # 6 slow_down responses → interval goes 5→10→15→20→25→30→30
    responses = [
        pre_resp,
        device_resp,
        verify_uri_resp,
        verify_resp,
        approve_resp,
    ] + [slow_down_resp] * 6

    with (
        patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
        _patch_pkce_as_unavailable,
        patch(
            "app.control.account.sso_build.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
    ):
        mock_cls.return_value = _make_session(responses)
        with pytest.raises(RuntimeError, match="no more mock"):
            await convert_sso_to_build("sso-token")

    sleep_args = [call[0][0] for call in mock_sleep.call_args_list]
    assert len(sleep_args) >= 6
    expected_intervals = [10, 15, 20, 25, 30, 30]
    for i, expected in enumerate(expected_intervals):
        assert sleep_args[i] == expected, (
            f"sleep {i}: expected {expected}, got {sleep_args[i]}"
        )


@pytest.mark.asyncio
async def test_device_flow_access_denied(
    _patch_pkce_as_unavailable: Any, _patch_sleep: Any
) -> None:
    """Device Flow access_denied → PermissionError."""
    device_resp = _make_ctx_resp(
        {
            "device_code": "dc-123",
            "user_code": "UC-ABC",
            "verification_uri_complete": "https://auth.x.ai/activate?user_code=UC-ABC",
            "interval": 5,
            "expires_in": 1800,
        }
    )
    pre_resp = _make_url_resp(status=200, url="https://accounts.x.ai/")
    verify_uri_resp = _make_url_resp(status=200)
    verify_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/consent")
    approve_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/done")
    denied_resp = _make_ctx_resp({"error": "access_denied"}, status=400)

    responses = [
        pre_resp,
        device_resp,
        verify_uri_resp,
        verify_resp,
        approve_resp,
        denied_resp,
    ]

    with (
        patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
        _patch_pkce_as_unavailable,
        _patch_sleep,
    ):
        mock_cls.return_value = _make_session(responses)
        with pytest.raises(PermissionError, match="denied"):
            await convert_sso_to_build("sso-token")


@pytest.mark.asyncio
async def test_device_flow_expired_token(
    _patch_pkce_as_unavailable: Any, _patch_sleep: Any
) -> None:
    """Device Flow expired_token → PermissionError."""
    device_resp = _make_ctx_resp(
        {
            "device_code": "dc-123",
            "user_code": "UC-ABC",
            "verification_uri_complete": "https://auth.x.ai/activate?user_code=UC-ABC",
            "interval": 5,
            "expires_in": 1800,
        }
    )
    pre_resp = _make_url_resp(status=200, url="https://accounts.x.ai/")
    verify_uri_resp = _make_url_resp(status=200)
    verify_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/consent")
    approve_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/done")
    expired_resp = _make_ctx_resp({"error": "expired_token"}, status=400)

    responses = [
        pre_resp,
        device_resp,
        verify_uri_resp,
        verify_resp,
        approve_resp,
        expired_resp,
    ]

    with (
        patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
        _patch_pkce_as_unavailable,
        _patch_sleep,
    ):
        mock_cls.return_value = _make_session(responses)
        with pytest.raises(PermissionError, match="denied"):
            await convert_sso_to_build("sso-token")


@pytest.mark.asyncio
async def test_device_flow_empty_token(
    _patch_pkce_as_unavailable: Any, _patch_sleep: Any
) -> None:
    """Empty SSO token after normalization → ValueError."""
    with patch("app.control.account.sso_build.get_config"):
        with pytest.raises(ValueError, match="Empty SSO token"):
            await convert_sso_to_build("")


# ═══════════════════════════════════════════════════════════════════════════
# Device Flow boundary condition tests
# ═══════════════════════════════════════════════════════════════════════════


class TestDeviceFlowMint:
    """Boundary condition tests for the Device Flow mint path."""

    @pytest.mark.asyncio
    async def test_empty_verification_uri(self) -> None:
        """verification_uri_complete="" → safe_xai_url("") is False → ValueError."""
        device_resp = _make_ctx_resp(
            {
                "device_code": "dc-123",
                "user_code": "UC-ABC",
                "verification_uri_complete": "",
                "interval": 5,
                "expires_in": 1800,
            }
        )
        pre_resp = _make_url_resp(status=200, url="https://accounts.x.ai/")

        responses = [pre_resp, device_resp]

        with (
            patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
            patch(
                "app.control.account.sso_build._mint_via_pkce_cs",
                new_callable=AsyncMock,
                side_effect=RuntimeError("PKCE unavailable"),
            ),
        ):
            mock_cls.return_value = _make_session(responses)
            with pytest.raises(ValueError, match="Incomplete device flow"):
                await convert_sso_to_build("sso-token")

    @pytest.mark.asyncio
    async def test_poll_server_error_500(self) -> None:
        """Poll returns status 500 with error body → RuntimeError."""
        device_resp = _make_ctx_resp(
            {
                "device_code": "dc-123",
                "user_code": "UC-ABC",
                "verification_uri_complete": "https://auth.x.ai/activate?user_code=UC-ABC",
                "interval": 5,
                "expires_in": 1800,
            }
        )
        pre_resp = _make_url_resp(status=200, url="https://accounts.x.ai/")
        verify_uri_resp = _make_url_resp(status=200)
        verify_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/consent")
        approve_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/done")
        error_resp = _make_ctx_resp(
            {"error": "server_error", "error_description": "Internal error"},
            status=500,
        )

        responses = [
            pre_resp,
            device_resp,
            verify_uri_resp,
            verify_resp,
            approve_resp,
            error_resp,
        ]

        with (
            patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
            patch(
                "app.control.account.sso_build._mint_via_pkce_cs",
                new_callable=AsyncMock,
                side_effect=RuntimeError("PKCE unavailable"),
            ),
            patch(
                "app.control.account.sso_build.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            mock_cls.return_value = _make_session(responses)
            with pytest.raises(RuntimeError):
                await convert_sso_to_build("sso-token")

    @pytest.mark.asyncio
    async def test_poll_rate_limit_429(self) -> None:
        """Poll returns status 429 → treated as unexpected → RuntimeError."""
        device_resp = _make_ctx_resp(
            {
                "device_code": "dc-123",
                "user_code": "UC-ABC",
                "verification_uri_complete": "https://auth.x.ai/activate?user_code=UC-ABC",
                "interval": 5,
                "expires_in": 1800,
            }
        )
        pre_resp = _make_url_resp(status=200, url="https://accounts.x.ai/")
        verify_uri_resp = _make_url_resp(status=200)
        verify_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/consent")
        approve_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/done")
        error_resp = _make_ctx_resp(
            {"error": "rate_limited", "error_description": "Too many requests"},
            status=429,
        )

        responses = [
            pre_resp,
            device_resp,
            verify_uri_resp,
            verify_resp,
            approve_resp,
            error_resp,
        ]

        with (
            patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
            patch(
                "app.control.account.sso_build._mint_via_pkce_cs",
                new_callable=AsyncMock,
                side_effect=RuntimeError("PKCE unavailable"),
            ),
            patch(
                "app.control.account.sso_build.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            mock_cls.return_value = _make_session(responses)
            with pytest.raises(RuntimeError):
                await convert_sso_to_build("sso-token")

    @pytest.mark.asyncio
    async def test_slow_down_multiple_times(self) -> None:
        """slow_down returned multiple times → interval increases up to 30s cap, then succeeds."""
        device_resp = _make_ctx_resp(
            {
                "device_code": "dc-123",
                "user_code": "UC-ABC",
                "verification_uri_complete": "https://auth.x.ai/activate?user_code=UC-ABC",
                "interval": 5,
                "expires_in": 1800,
            }
        )
        pre_resp = _make_url_resp(status=200, url="https://accounts.x.ai/")
        verify_uri_resp = _make_url_resp(status=200)
        verify_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/consent")
        approve_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/done")
        slow_down_resp = _make_ctx_resp({"error": "slow_down"}, status=400)
        token_resp = _make_ctx_resp({"access_token": "at-device", "expires_in": 7200})

        # 6 slow_down → interval goes 5→10→15→20→25→30→30, then success
        responses = (
            [
                pre_resp,
                device_resp,
                verify_uri_resp,
                verify_resp,
                approve_resp,
            ]
            + [slow_down_resp] * 6
            + [token_resp]
        )

        with (
            patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
            patch(
                "app.control.account.sso_build._mint_via_pkce_cs",
                new_callable=AsyncMock,
                side_effect=RuntimeError("PKCE unavailable"),
            ),
            patch(
                "app.control.account.sso_build.asyncio.sleep",
                new_callable=AsyncMock,
            ) as mock_sleep,
        ):
            mock_cls.return_value = _make_session(responses)
            result = await convert_sso_to_build("sso-token")

        assert result.access_token == "at-device"
        sleep_args = [c[0][0] for c in mock_sleep.call_args_list]
        expected = [10, 15, 20, 25, 30, 30]
        for i, exp in enumerate(expected):
            assert sleep_args[i] == exp, (
                f"sleep {i}: expected {exp}s, got {sleep_args[i]}s"
            )

    @pytest.mark.asyncio
    async def test_interval_zero_response(self) -> None:
        """Device endpoint returns interval=0 → defaults to 5s."""
        device_resp = _make_ctx_resp(
            {
                "device_code": "dc-123",
                "user_code": "UC-ABC",
                "verification_uri_complete": "https://auth.x.ai/activate?user_code=UC-ABC",
                "interval": 0,
                "expires_in": 1800,
            }
        )
        pre_resp = _make_url_resp(status=200, url="https://accounts.x.ai/")
        verify_uri_resp = _make_url_resp(status=200)
        verify_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/consent")
        approve_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/done")
        token_resp = _make_ctx_resp({"access_token": "at-device", "expires_in": 7200})

        responses = [
            pre_resp,
            device_resp,
            verify_uri_resp,
            verify_resp,
            approve_resp,
            token_resp,
        ]

        with (
            patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
            patch(
                "app.control.account.sso_build._mint_via_pkce_cs",
                new_callable=AsyncMock,
                side_effect=RuntimeError("PKCE unavailable"),
            ),
            patch(
                "app.control.account.sso_build.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            mock_cls.return_value = _make_session(responses)
            result = await convert_sso_to_build("sso-token")

        assert result.access_token == "at-device"
        assert result.expires_in == 7200

    @pytest.mark.asyncio
    async def test_expires_in_zero_response(self) -> None:
        """Device endpoint returns expires_in=0 → defaults to 1800s."""
        device_resp = _make_ctx_resp(
            {
                "device_code": "dc-123",
                "user_code": "UC-ABC",
                "verification_uri_complete": "https://auth.x.ai/activate?user_code=UC-ABC",
                "interval": 5,
                "expires_in": 0,
            }
        )
        pre_resp = _make_url_resp(status=200, url="https://accounts.x.ai/")
        verify_uri_resp = _make_url_resp(status=200)
        verify_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/consent")
        approve_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/done")
        token_resp = _make_ctx_resp({"access_token": "at-device", "expires_in": 7200})

        responses = [
            pre_resp,
            device_resp,
            verify_uri_resp,
            verify_resp,
            approve_resp,
            token_resp,
        ]

        with (
            patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
            patch(
                "app.control.account.sso_build._mint_via_pkce_cs",
                new_callable=AsyncMock,
                side_effect=RuntimeError("PKCE unavailable"),
            ),
            patch(
                "app.control.account.sso_build.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            mock_cls.return_value = _make_session(responses)
            result = await convert_sso_to_build("sso-token")

        assert result.access_token == "at-device"
        assert result.expires_in == 7200

    @pytest.mark.asyncio
    async def test_verify_final_url_no_consent(self) -> None:
        """Verify returns 200 but final URL lacks 'consent' → RuntimeError."""
        device_resp = _make_ctx_resp(
            {
                "device_code": "dc-123",
                "user_code": "UC-ABC",
                "verification_uri_complete": "https://auth.x.ai/activate?user_code=UC-ABC",
                "interval": 5,
                "expires_in": 1800,
            }
        )
        pre_resp = _make_url_resp(status=200, url="https://accounts.x.ai/")
        verify_uri_resp = _make_url_resp(status=200)
        verify_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/denied")

        responses = [pre_resp, device_resp, verify_uri_resp, verify_resp]

        with (
            patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
            patch(
                "app.control.account.sso_build._mint_via_pkce_cs",
                new_callable=AsyncMock,
                side_effect=RuntimeError("PKCE unavailable"),
            ),
            patch(
                "app.control.account.sso_build.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            mock_cls.return_value = _make_session(responses)
            with pytest.raises(RuntimeError, match="no.*consent"):
                await convert_sso_to_build("sso-token")

    @pytest.mark.asyncio
    async def test_approve_final_url_no_done(self) -> None:
        """Approve returns 200 but final URL lacks 'done' → RuntimeError."""
        device_resp = _make_ctx_resp(
            {
                "device_code": "dc-123",
                "user_code": "UC-ABC",
                "verification_uri_complete": "https://auth.x.ai/activate?user_code=UC-ABC",
                "interval": 5,
                "expires_in": 1800,
            }
        )
        pre_resp = _make_url_resp(status=200, url="https://accounts.x.ai/")
        verify_uri_resp = _make_url_resp(status=200)
        verify_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/consent")
        approve_resp = _make_url_resp(
            status=200, url="https://auth.x.ai/device/finished"
        )

        responses = [pre_resp, device_resp, verify_uri_resp, verify_resp, approve_resp]

        with (
            patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
            patch(
                "app.control.account.sso_build._mint_via_pkce_cs",
                new_callable=AsyncMock,
                side_effect=RuntimeError("PKCE unavailable"),
            ),
            patch(
                "app.control.account.sso_build.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            mock_cls.return_value = _make_session(responses)
            with pytest.raises(RuntimeError, match="no.*done"):
                await convert_sso_to_build("sso-token")

    @pytest.mark.asyncio
    async def test_sso_validation_redirect_to_signup(self) -> None:
        """GET accounts.x.ai/ redirects to sign-up → PermissionError."""
        pre_resp = _make_url_resp(
            status=200,
            url="https://accounts.x.ai/sign-up",
        )

        with (
            patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
            patch(
                "app.control.account.sso_build._mint_via_pkce_cs",
                new_callable=AsyncMock,
                side_effect=RuntimeError("PKCE unavailable"),
            ),
            patch(
                "app.control.account.sso_build.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            mock_cls.return_value = _make_session([pre_resp])
            with pytest.raises(PermissionError, match="invalid or expired"):
                await convert_sso_to_build("sso-token")

    @pytest.mark.asyncio
    async def test_sso_validation_status_unauthorized(self) -> None:
        """GET accounts.x.ai/ returns 401 → PermissionError."""
        pre_resp = _make_url_resp(status=401, url="https://accounts.x.ai/login")

        with (
            patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
            patch(
                "app.control.account.sso_build._mint_via_pkce_cs",
                new_callable=AsyncMock,
                side_effect=RuntimeError("PKCE unavailable"),
            ),
            patch(
                "app.control.account.sso_build.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            mock_cls.return_value = _make_session([pre_resp])
            with pytest.raises(PermissionError, match="invalid or expired"):
                await convert_sso_to_build("sso-token")


# ═══════════════════════════════════════════════════════════════════════════
# Device Flow regression tests
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_old_device_flow_regression_no_direct_poll_without_approve() -> None:
    """Verify approve step happens before poll — old bug skipped approve."""
    device_resp = _make_ctx_resp(
        {
            "device_code": "dc-123",
            "user_code": "UC-ABC",
            "verification_uri_complete": "https://auth.x.ai/activate?user_code=UC-ABC",
            "interval": 5,
            "expires_in": 1800,
        }
    )
    pre_resp = _make_url_resp(status=200, url="https://accounts.x.ai/")
    verify_uri_resp = _make_url_resp(status=200)
    verify_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/consent")
    approve_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/done")
    pending_resp = _make_ctx_resp({"error": "authorization_pending"}, status=400)
    token_resp = _make_ctx_resp({"access_token": "at-device", "expires_in": 7200})

    # approve_resp MUST precede poll responses — if approve is skipped,
    # the mock queue order breaks and the test fails
    responses = [
        pre_resp,
        device_resp,
        verify_uri_resp,
        verify_resp,
        approve_resp,
        pending_resp,
        token_resp,
    ]

    with (
        patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
        patch(
            "app.control.account.sso_build._mint_via_pkce_cs",
            new_callable=AsyncMock,
            side_effect=RuntimeError("PKCE unavailable"),
        ),
        patch(
            "app.control.account.sso_build.asyncio.sleep",
            new_callable=AsyncMock,
        ),
    ):
        mock_cls.return_value = _make_session(responses)
        result = await convert_sso_to_build("sso-token")

    assert result.access_token == "at-device"
    assert result.expires_in == 7200


@pytest.mark.asyncio
async def test_build_credential_seed_dict_compatibility() -> None:
    """Verify BuildCredentialSeed from Device Flow supports dict-like access."""
    device_resp = _make_ctx_resp(
        {
            "device_code": "dc-123",
            "user_code": "UC-ABC",
            "verification_uri_complete": "https://auth.x.ai/activate?user_code=UC-ABC",
            "interval": 5,
            "expires_in": 1800,
        }
    )
    pre_resp = _make_url_resp(status=200, url="https://accounts.x.ai/")
    verify_uri_resp = _make_url_resp(status=200)
    verify_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/consent")
    approve_resp = _make_url_resp(status=200, url="https://auth.x.ai/device/done")
    token_resp = _make_ctx_resp(
        {
            "access_token": "at-dict",
            "refresh_token": "rt-dict",
            "expires_in": 7200,
        }
    )

    responses = [
        pre_resp,
        device_resp,
        verify_uri_resp,
        verify_resp,
        approve_resp,
        token_resp,
    ]

    with (
        patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
        patch(
            "app.control.account.sso_build._mint_via_pkce_cs",
            new_callable=AsyncMock,
            side_effect=RuntimeError("PKCE unavailable"),
        ),
        patch(
            "app.control.account.sso_build.asyncio.sleep",
            new_callable=AsyncMock,
        ),
    ):
        mock_cls.return_value = _make_session(responses)
        seed = await convert_sso_to_build("sso-token")

    # Dict-like access patterns used by tokens.py for backward compatibility
    assert seed["access_token"] == "at-dict"
    assert seed.get("refresh_token") == "rt-dict"
    assert seed.get("expires_in", "3600") == "7200"
    assert seed["source_key"].startswith("sso-build:")
    assert seed.get("email", "") == ""
    assert seed.get("nonexistent", "fallback") == "fallback"


# ═══════════════════════════════════════════════════════════════════════════
# BuildCredentialSeed backward-compatibility tests
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildCredentialSeed:
    """Test backward-compatible dict interface for existing callers."""

    def test_dict_like_access(self) -> None:
        seed = BuildCredentialSeed(
            access_token="at-1",
            refresh_token="rt-1",
            id_token="id-1",
            expires_in=7200,
            email="test@x.ai",
        )
        assert seed["access_token"] == "at-1"
        assert seed["refresh_token"] == "rt-1"
        assert seed["id_token"] == "id-1"
        assert seed["expires_in"] == "7200"
        assert seed["email"] == "test@x.ai"

    def test_dict_get(self) -> None:
        seed = BuildCredentialSeed(access_token="at-1")
        assert seed.get("access_token") == "at-1"
        assert seed.get("nonexistent", "default") == "default"

    def test_dict_get_with_default(self) -> None:
        """Caller pattern: creds.get('expires_in', '3600')"""
        seed = BuildCredentialSeed()  # no expires_in set → default from __getitem__
        # The dataclass default is 21600, so get returns str(21600)
        assert seed.get("expires_in", "3600") == "21600"

    def test_dict_key_error(self) -> None:
        seed = BuildCredentialSeed()
        with pytest.raises(KeyError):
            _ = seed["nonexistent"]

    def test_attribute_access(self) -> None:
        seed = BuildCredentialSeed(
            access_token="at-1",
            email="test@x.ai",
            user_id="u1",
            team_id="t1",
            source_key="sso-build:h1",
        )
        assert seed.access_token == "at-1"
        assert seed.email == "test@x.ai"
        assert seed.user_id == "u1"
        assert seed.team_id == "t1"
        assert seed.source_key == "sso-build:h1"
        assert seed.oidc_client_id == "b1a00492-073a-47ea-816f-4c329264a828"

    def test_defaults(self) -> None:
        seed = BuildCredentialSeed()
        assert seed.provider == "grok_build"
        assert seed.auth_type == "oauth"
        assert seed.oidc_client_id == "b1a00492-073a-47ea-816f-4c329264a828"


# ═══════════════════════════════════════════════════════════════════════════
# Module exports test
# ═══════════════════════════════════════════════════════════════════════════


def test_all_exports() -> None:
    """Verify __all__ exports the expected symbols."""
    from app.control.account import sso_build

    expected = {
        "BuildCredentialSeed",
        "convert_sso_to_build",
        "normalize_sso_token",
        "safe_xai_url",
        "decode_build_claims",
    }
    assert set(sso_build.__all__) == expected


# ═══════════════════════════════════════════════════════════════════════════
# CF clearance resolution (M1 bug: mint paths read non-existent config keys)
# ═══════════════════════════════════════════════════════════════════════════


class _StubCfg:
    """Minimal config stub exposing get_str(key, default) like the snapshot."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def get_str(self, key: str, default: str = "") -> str:
        value = self.data.get(key, default)
        return str(value) if value is not None else default


def test_resolve_cf_clearance_from_cookies() -> None:
    """Schema key proxy.clearance.cf_cookies derives the cf_clearance value."""
    from app.control.account.sso_build import _resolve_cf_clearance_value

    cfg = _StubCfg({"proxy.clearance.cf_cookies": "cf_clearance=abc123; foo=1"})
    assert _resolve_cf_clearance_value(cfg) == "abc123"


def test_resolve_cf_clearance_legacy_fallback() -> None:
    """Legacy flat key proxy.cf_clearance still resolves."""
    from app.control.account.sso_build import _resolve_cf_clearance_value

    cfg = _StubCfg({"proxy.cf_clearance": "legacy"})
    assert _resolve_cf_clearance_value(cfg) == "legacy"


def test_resolve_cf_clearance_empty() -> None:
    """No configured clearance resolves to empty string."""
    from app.control.account.sso_build import _resolve_cf_clearance_value

    assert _resolve_cf_clearance_value(_StubCfg({})) == ""
