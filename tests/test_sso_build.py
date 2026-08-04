"""Tests for SSO→Build credential conversion.

Tests cover both PKCE-CS path (preferred) and Device Flow path (fallback),
plus all utility functions ported from Go sso_build.go.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.control.account import sso_build
from app.control.account.sso_build import (
    BuildCredentialSeed,
    SSOCredentialRejected,
    convert_sso_to_build,
    decode_build_claims,
    normalize_sso_token,
    safe_xai_url,
)
from app.control.proxy.config import ClearanceConfig
from app.dataplane.proxy.adapters.profile import ProxyProfile

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

    def test_normalize_sso_token_full_cookie_header(self) -> None:
        """Full 'Cookie:' header → extract the sso field value."""
        assert (
            normalize_sso_token("Cookie: foo=bar; sso=abc123; sso-rw=abc123")
            == "abc123"
        )

    def test_normalize_sso_token_cookie_prefix(self) -> None:
        """Lowercase 'cookie:' prefix with only sso-rw field → extract it."""
        assert normalize_sso_token("cookie: sso-rw=xyz; other=1") == "xyz"

    def test_normalize_sso_token_plain_sso_prefix(self) -> None:
        """Bare 'sso=xxx' input (existing behavior) → unchanged."""
        assert normalize_sso_token("sso=token123") == "token123"

    def test_normalize_sso_token_plain_raw(self) -> None:
        """Bare raw token (existing behavior) → unchanged."""
        assert normalize_sso_token("rawtoken") == "rawtoken"


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
# convert_sso_to_build orchestration tests (Device Flow first, PKCE-CS fallback)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_device_flow_success_short_circuits() -> None:
    """Device Flow succeeds first → PKCE-CS never runs."""
    with (
        patch(
            "app.control.account.sso_build._mint_via_device_flow",
            new_callable=AsyncMock,
        ) as mock_device,
        patch(
            "app.control.account.sso_build._mint_via_pkce_cs",
            new_callable=AsyncMock,
        ) as mock_pkce,
    ):
        mock_device.return_value = BuildCredentialSeed(
            access_token="at-device",
            refresh_token="rt-device",
            id_token="id-device",
            expires_in=21600,
            name="test@x.ai",
            email="test@x.ai",
            user_id="user123",
            team_id="team456",
            source_key="sso-build:abc123",
        )

        result = await convert_sso_to_build("good-sso-token")

    assert result.access_token == "at-device"
    assert result.email == "test@x.ai"
    assert result.expires_in == 21600
    mock_device.assert_called_once()
    mock_pkce.assert_not_called()


@pytest.mark.asyncio
async def test_convert_skips_pkce_when_device_succeeds() -> None:
    """Device Flow success → PKCE-CS fallback not touched."""
    with (
        patch(
            "app.control.account.sso_build._mint_via_device_flow",
            new_callable=AsyncMock,
        ) as mock_device,
        patch(
            "app.control.account.sso_build._mint_via_pkce_cs",
            new_callable=AsyncMock,
        ) as mock_pkce,
    ):
        mock_device.return_value = BuildCredentialSeed(
            access_token="at-device", expires_in=7200
        )

        result = await convert_sso_to_build("sso-token")

    assert result.access_token == "at-device"
    mock_device.assert_called_once()
    mock_pkce.assert_not_called()


@pytest.mark.asyncio
async def test_device_flow_first_pkce_fallback() -> None:
    """Device Flow fails (transient) → falls back to PKCE-CS → returns PKCE result."""
    with (
        patch(
            "app.control.account.sso_build._mint_via_device_flow",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Device Flow: network timeout"),
        ) as mock_device,
        patch(
            "app.control.account.sso_build._mint_via_pkce_cs",
            new_callable=AsyncMock,
        ) as mock_pkce,
    ):
        mock_pkce.return_value = BuildCredentialSeed(
            access_token="at-pkce",
            refresh_token="rt-pkce",
            expires_in=21600,
        )

        result = await convert_sso_to_build("sso-token")

    assert result.access_token == "at-pkce"
    assert result.expires_in == 21600
    mock_device.assert_called_once()
    mock_pkce.assert_called_once()


@pytest.mark.asyncio
async def test_both_paths_fail_propagates_last_error() -> None:
    """Device Flow fails and PKCE-CS fallback also fails → propagate PKCE error."""
    with (
        patch(
            "app.control.account.sso_build._mint_via_device_flow",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Device Flow timed out"),
        ) as mock_device,
        patch(
            "app.control.account.sso_build._mint_via_pkce_cs",
            new_callable=AsyncMock,
            side_effect=TimeoutError("PKCE-CS down"),
        ),
        pytest.raises(TimeoutError, match="PKCE-CS down"),
    ):
        await convert_sso_to_build("sso-token")

    mock_device.assert_called_once()


@pytest.mark.asyncio
async def test_pkce_fallback_hard_failure_propagates() -> None:
    """Device Flow fails → PKCE-CS fallback raises SSOCredentialRejected → propagates."""
    from app.control.account.sso_build import SSOCredentialRejected

    with (
        patch(
            "app.control.account.sso_build._mint_via_device_flow",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Device Flow: network timeout"),
        ) as mock_device,
        patch(
            "app.control.account.sso_build._mint_via_pkce_cs",
            new_callable=AsyncMock,
            side_effect=SSOCredentialRejected(
                "PKCE-CS credential rejected: "
                "Bad credentials [WKE=unauthenticated:bad-credentials]"
            ),
        ),
        pytest.raises(SSOCredentialRejected),
    ):
        await convert_sso_to_build("sso-token")

    mock_device.assert_called_once()


@pytest.mark.asyncio
async def test_device_flow_permission_error_no_pkce_fallback() -> None:
    """Device Flow PermissionError (invalid SSO) propagates — PKCE-CS not attempted."""
    with (
        patch(
            "app.control.account.sso_build._mint_via_device_flow",
            new_callable=AsyncMock,
            side_effect=PermissionError(
                "SSO token invalid or expired: redirected to sign-in"
            ),
        ) as mock_device,
        patch(
            "app.control.account.sso_build._mint_via_pkce_cs",
            new_callable=AsyncMock,
        ) as mock_pkce,
        pytest.raises(PermissionError, match="invalid or expired"),
    ):
        await convert_sso_to_build("sso-token")

    mock_device.assert_called_once()
    mock_pkce.assert_not_called()


@pytest.mark.asyncio
async def test_pkce_cs_bad_credentials_classification() -> None:
    """gRPC hard-failure message → SSOCredentialRejected (not RuntimeError)."""
    from app.control.account import sso_build
    from app.control.account.sso_build import SSOCredentialRejected

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = AsyncMock(return_value=MagicMock())
    grpc_resp = MagicMock()
    grpc_resp.headers = {
        "grpc-message": "Bad credentials [WKE=unauthenticated:bad-credentials]"
    }
    grpc_resp.content = b"ignored"
    mock_session.post = AsyncMock(return_value=grpc_resp)

    with (
        patch("curl_cffi.requests.AsyncSession", return_value=mock_session),
        patch(
            "app.control.account.sso_build._acquire_mint_lease",
            new=AsyncMock(return_value=None),
            create=True,
        ),
        patch(
            "app.control.account.sso_build._resolve_mint_profile",
            new=AsyncMock(
                return_value=ProxyProfile(cf_cookies="", user_agent="", cf_clearance="")
            ),
            create=True,
        ),
        patch(
            "app.control.account.sso_build._resolve_cf_clearance_value",
            new=AsyncMock(return_value=""),
            create=True,
        ),
        patch(
            "app.dataplane.proxy.adapters.session.build_session_kwargs",
            return_value={},
        ),
        patch(
            "app.control.account.sso_build._grpc_parse_response",
            return_value={"grpc_status": 3, "messages": [], "trailers": {}},
        ),
        pytest.raises(SSOCredentialRejected) as exc_info,
    ):
        await sso_build._mint_via_pkce_cs("sso-token")

    assert exc_info.value.credential_rejected is True
    assert "bad-credentials" in str(exc_info.value)


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
    """Invalid SSO token → SSOCredentialRejected from pre-validation."""
    pre_resp = _make_url_resp(status=200, url="https://accounts.x.ai/sign-in")

    with (
        patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
        _patch_pkce_as_unavailable,
        _patch_sleep,
    ):
        mock_cls.return_value = _make_session([pre_resp])
        with pytest.raises(SSOCredentialRejected, match="invalid or expired"):
            await convert_sso_to_build("invalid-token")


@pytest.mark.asyncio
async def test_device_flow_unauthorized_status(
    _patch_pkce_as_unavailable: Any, _patch_sleep: Any
) -> None:
    """Pre-validation returns 401 → SSOCredentialRejected."""
    pre_resp = _make_url_resp(status=401, url="https://accounts.x.ai/login")

    with (
        patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
        _patch_pkce_as_unavailable,
        _patch_sleep,
    ):
        mock_cls.return_value = _make_session([pre_resp])
        with pytest.raises(SSOCredentialRejected, match="invalid or expired"):
            await convert_sso_to_build("expired-token")


@pytest.mark.asyncio
async def test_device_flow_precheck_invalid_sso_raises_rejected(
    _patch_pkce_as_unavailable: Any, _patch_sleep: Any
) -> None:
    """Pre-validation sign-in redirect → SSOCredentialRejected (marks source account)."""
    pre_resp = _make_url_resp(status=200, url="https://accounts.x.ai/sign-in")

    with (
        patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
        _patch_pkce_as_unavailable,
        _patch_sleep,
    ):
        mock_cls.return_value = _make_session([pre_resp])
        with pytest.raises(SSOCredentialRejected, match="invalid or expired"):
            await convert_sso_to_build("invalid-sso-token")


@pytest.mark.asyncio
async def test_convert_device_precheck_rejected_no_fallback() -> None:
    """Device Flow raises SSOCredentialRejected → propagates, PKCE-CS never runs."""
    from app.control.account.sso_build import SSOCredentialRejected

    with (
        patch(
            "app.control.account.sso_build._mint_via_device_flow",
            new_callable=AsyncMock,
            side_effect=SSOCredentialRejected(
                "SSO token invalid or expired: redirected to sign-in"
            ),
        ) as mock_device,
        patch(
            "app.control.account.sso_build._mint_via_pkce_cs",
            new_callable=AsyncMock,
        ) as mock_pkce,
        pytest.raises(SSOCredentialRejected, match="invalid or expired"),
    ):
        await convert_sso_to_build("invalid-sso-token")

    mock_device.assert_called_once()
    mock_pkce.assert_not_called()


@pytest.mark.asyncio
async def test_device_flow_poll_expired_token_still_permission_error(
    _patch_pkce_as_unavailable: Any, _patch_sleep: Any
) -> None:
    """Poll expired_token stays PermissionError — transient, must NOT mark account."""
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
async def test_device_flow_verify_no_consent(_patch_sleep: Any) -> None:
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
        _patch_sleep,
    ):
        mock_cls.return_value = _make_session(responses)
        with pytest.raises(RuntimeError, match="no.*consent"):
            await sso_build._mint_via_device_flow("sso-token")


@pytest.mark.asyncio
async def test_device_flow_approve_no_done(_patch_sleep: Any) -> None:
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
        _patch_sleep,
    ):
        mock_cls.return_value = _make_session(responses)
        with pytest.raises(RuntimeError, match="no.*done"):
            await sso_build._mint_via_device_flow("sso-token")


@pytest.mark.asyncio
async def test_device_flow_approve_fails(_patch_sleep: Any) -> None:
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
        _patch_sleep,
    ):
        mock_cls.return_value = _make_session(responses)
        with pytest.raises(RuntimeError, match="Device approve failed"):
            await sso_build._mint_via_device_flow("sso-token")


@pytest.mark.asyncio
async def test_device_flow_timeout(_patch_sleep: Any) -> None:
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
        _patch_sleep,
        _patch_monotonic_with_advance(start=0.0, delta=5.0),
    ):
        mock_cls.return_value = _make_session(responses)
        with pytest.raises(TimeoutError, match="timed out"):
            await sso_build._mint_via_device_flow("sso-token")


@pytest.mark.asyncio
async def test_device_flow_slow_down_backoff(_patch_sleep: Any) -> None:
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
        patch(
            "app.control.account.sso_build.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep,
    ):
        mock_cls.return_value = _make_session(responses)
        with pytest.raises(RuntimeError, match="no more mock"):
            await sso_build._mint_via_device_flow("sso-token")

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
    with (
        patch("app.control.account.sso_build.get_config"),
        pytest.raises(ValueError, match="Empty SSO token"),
    ):
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

        with patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls:
            mock_cls.return_value = _make_session(responses)
            with pytest.raises(ValueError, match="Incomplete device flow"):
                await sso_build._mint_via_device_flow("sso-token")

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
                "app.control.account.sso_build.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            mock_cls.return_value = _make_session(responses)
            with pytest.raises(RuntimeError):
                await sso_build._mint_via_device_flow("sso-token")

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
                "app.control.account.sso_build.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            mock_cls.return_value = _make_session(responses)
            with pytest.raises(RuntimeError):
                await sso_build._mint_via_device_flow("sso-token")

    @pytest.mark.asyncio
    async def test_device_flow_seeds_sso_and_sso_rw(self, _patch_sleep: Any) -> None:
        """Device Flow seeds both sso and sso-rw cookies on auth.x.ai + accounts.x.ai.

        Approve is a write op that requires sso-rw; seeding only sso leaves it
        unauthenticated and the token poll stuck on authorization_pending.
        Mirrors Go sso_device.go: `cookies: {"sso": tok, "sso-rw": tok}`.
        """
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
        token_resp = _make_ctx_resp({"access_token": "at-device", "expires_in": 7200})

        responses = [
            pre_resp,
            device_resp,
            verify_uri_resp,
            verify_resp,
            approve_resp,
            token_resp,
        ]

        jar = MagicMock()
        with (
            patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
            patch("app.control.account.sso_build.aiohttp.CookieJar", return_value=jar),
            _patch_sleep,
        ):
            mock_cls.return_value = _make_session(responses)
            await sso_build._mint_via_device_flow("sso-token")

        assert mock_cls.call_args.kwargs["cookie_jar"] is jar
        sso_seeds = {
            str(call.args[1]): dict(call.args[0])
            for call in jar.update_cookies.call_args_list
            if "sso" in call.args[0]
        }
        assert sso_seeds["https://auth.x.ai"] == {
            "sso": "sso-token",
            "sso-rw": "sso-token",
        }
        assert sso_seeds["https://accounts.x.ai"] == {
            "sso": "sso-token",
            "sso-rw": "sso-token",
        }

    @pytest.mark.asyncio
    async def test_device_flow_poll_http_400_fails_fast(
        self, _patch_sleep: Any
    ) -> None:
        """Poll 400 with unknown error → RuntimeError immediately (no 75s wait)."""
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
        error_resp = _make_ctx_resp({}, status=400)

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
            _patch_sleep,
        ):
            mock_cls.return_value = _make_session(responses)
            with pytest.raises(RuntimeError, match="poll failed"):
                await sso_build._mint_via_device_flow("sso-token")

    @pytest.mark.asyncio
    async def test_device_flow_poll_403_fails_fast(self, _patch_sleep: Any) -> None:
        """Poll 403 (CF challenge) → RuntimeError immediately, not a 75s wait."""
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
            {"error": "cf_challenge", "error_description": "Cloudflare challenge"},
            status=403,
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
            _patch_sleep,
        ):
            mock_cls.return_value = _make_session(responses)
            with pytest.raises(RuntimeError, match="poll failed"):
                await sso_build._mint_via_device_flow("sso-token")

    @pytest.mark.asyncio
    async def test_device_flow_poll_200_pending_continues(
        self, _patch_sleep: Any
    ) -> None:
        """Poll 200 + authorization_pending → keeps polling until timeout (no fail-fast)."""
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
        pending_resp = _make_ctx_resp({"error": "authorization_pending"}, status=200)

        responses = [
            pre_resp,
            device_resp,
            verify_uri_resp,
            verify_resp,
            approve_resp,
        ] + [pending_resp] * 15

        with (
            patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls,
            _patch_sleep,
            _patch_monotonic_with_advance(start=0.0, delta=5.0),
        ):
            mock_cls.return_value = _make_session(responses)
            with pytest.raises(TimeoutError, match="timed out"):
                await sso_build._mint_via_device_flow("sso-token")

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
                "app.control.account.sso_build.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            mock_cls.return_value = _make_session(responses)
            with pytest.raises(RuntimeError):
                await sso_build._mint_via_device_flow("sso-token")

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
                "app.control.account.sso_build.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            mock_cls.return_value = _make_session(responses)
            with pytest.raises(RuntimeError):
                await sso_build._mint_via_device_flow("sso-token")

    @pytest.mark.asyncio
    async def test_sso_validation_redirect_to_signup(self) -> None:
        """GET accounts.x.ai/ redirects to sign-up → SSOCredentialRejected."""
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
            with pytest.raises(SSOCredentialRejected, match="invalid or expired"):
                await convert_sso_to_build("sso-token")

    @pytest.mark.asyncio
    async def test_sso_validation_status_unauthorized(self) -> None:
        """GET accounts.x.ai/ returns 401 → SSOCredentialRejected."""
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
            with pytest.raises(SSOCredentialRejected, match="invalid or expired"):
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
        "SSOCredentialRejected",
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


@pytest.mark.asyncio
async def test_resolve_cf_clearance_from_cookies() -> None:
    """Schema key proxy.clearance.cf_cookies derives the cf_clearance value."""
    from app.control.account.sso_build import _resolve_cf_clearance_value

    cfg = _StubCfg({"proxy.clearance.cf_cookies": "cf_clearance=abc123; foo=1"})
    with patch("app.dataplane.proxy.get_proxy_runtime") as _mock:
        assert await _resolve_cf_clearance_value(cfg) == "abc123"


@pytest.mark.asyncio
async def test_resolve_cf_clearance_legacy_fallback() -> None:
    """Legacy flat key proxy.cf_clearance still resolves."""
    from app.control.account.sso_build import _resolve_cf_clearance_value

    cfg = _StubCfg({"proxy.cf_clearance": "legacy"})
    with patch("app.dataplane.proxy.get_proxy_runtime") as _mock:
        assert await _resolve_cf_clearance_value(cfg) == "legacy"


@pytest.mark.asyncio
async def test_resolve_cf_clearance_empty() -> None:
    """No configured clearance resolves to empty string."""
    from app.control.account.sso_build import _resolve_cf_clearance_value

    with patch("app.dataplane.proxy.get_proxy_runtime") as _mock:
        assert await _resolve_cf_clearance_value(_StubCfg({})) == ""


@pytest.mark.asyncio
async def test_resolve_cf_clearance_prefers_lease_bundle() -> None:
    """Lease cf_cookies (turnstile-solved) wins over config — 403 root cause."""
    from app.control.account.sso_build import _resolve_cf_clearance_value

    lease = MagicMock()
    lease.cf_cookies = "cf_clearance=turnstile-abc; other=1"
    proxy = AsyncMock()
    proxy.acquire.return_value = lease
    with patch(
        "app.dataplane.proxy.get_proxy_runtime", new=AsyncMock(return_value=proxy)
    ):
        cfg = _StubCfg({"proxy.clearance.cf_cookies": "cf_clearance=config-val"})
        assert await _resolve_cf_clearance_value(cfg) == "turnstile-abc"


@pytest.mark.asyncio
async def test_resolve_cf_clearance_lease_empty_falls_back_to_config() -> None:
    """Empty lease cf_cookies falls back to config — no hard failure."""
    from app.control.account.sso_build import _resolve_cf_clearance_value

    lease = MagicMock()
    lease.cf_cookies = ""
    proxy = AsyncMock()
    proxy.acquire.return_value = lease
    with patch(
        "app.dataplane.proxy.get_proxy_runtime", new=AsyncMock(return_value=proxy)
    ):
        cfg = _StubCfg({"proxy.clearance.cf_cookies": "cf_clearance=cfg-ok"})
        assert await _resolve_cf_clearance_value(cfg) == "cfg-ok"


# ═══════════════════════════════════════════════════════════════════════════
# SSO→Build mint profile resolution (UA fingerprint alignment with turnstile)
# ═══════════════════════════════════════════════════════════════════════════


class TestResolveMintProfile:
    """_resolve_mint_profile returns the full lease profile (cf_cookies + UA)."""

    @pytest.mark.asyncio
    async def test_mint_profile_from_lease(self) -> None:
        """Lease cf_cookies + user_agent → profile with matching clearance + UA."""
        from app.control.account import sso_build

        lease = MagicMock()
        lease.cf_cookies = "cf_clearance=tc; foo=1"
        lease.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
        )
        proxy = AsyncMock()
        proxy.acquire.return_value = lease
        with patch(
            "app.dataplane.proxy.get_proxy_runtime",
            new=AsyncMock(return_value=proxy),
        ):
            profile = await sso_build._resolve_mint_profile()

        assert profile.cf_clearance == "tc"
        assert profile.cf_cookies == "cf_clearance=tc; foo=1"
        assert profile.user_agent == lease.user_agent

    @pytest.mark.asyncio
    async def test_mint_profile_config_fallback(self) -> None:
        """Empty lease → profile falls back to configured clearance (full UA)."""
        from app.control.account import sso_build

        lease = MagicMock()
        lease.cf_cookies = ""
        lease.user_agent = ""
        proxy = AsyncMock()
        proxy.acquire.return_value = lease
        with (
            patch(
                "app.dataplane.proxy.get_proxy_runtime",
                new=AsyncMock(return_value=proxy),
            ),
            patch(
                "app.dataplane.proxy.adapters.profile.resolve_clearance_config",
                return_value=ClearanceConfig(
                    cf_cookies="cf_clearance=cfgval; x=1",
                    user_agent="UA-from-config",
                    cf_clearance="cfgval",
                    browser="chrome120",
                ),
            ),
        ):
            profile = await sso_build._resolve_mint_profile()

        assert profile.cf_clearance == "cfgval"
        assert profile.user_agent == "UA-from-config"

    @pytest.mark.asyncio
    async def test_mint_profile_acquire_failure_falls_back(self) -> None:
        """acquire() raising → config fallback instead of a hard failure."""
        from app.control.account import sso_build

        with (
            patch(
                "app.dataplane.proxy.get_proxy_runtime",
                new=AsyncMock(side_effect=RuntimeError("runtime down")),
            ),
            patch(
                "app.dataplane.proxy.adapters.profile.resolve_clearance_config",
                return_value=ClearanceConfig(
                    cf_cookies="cf_clearance=c; x=1",
                    user_agent="UA-x",
                    cf_clearance="c",
                    browser="chrome120",
                ),
            ),
        ):
            profile = await sso_build._resolve_mint_profile()

        assert profile.cf_clearance == "c"
        assert profile.user_agent == "UA-x"


@pytest.mark.asyncio
async def test_pkce_uses_profile_session_kwargs() -> None:
    """PKCE-CS session kwargs come from the lease profile, not hardcoded chrome131."""
    from app.control.account import sso_build

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = AsyncMock(side_effect=RuntimeError("network stopped"))

    with (
        patch("curl_cffi.requests.AsyncSession", return_value=mock_session),
        patch(
            "app.control.account.sso_build._acquire_mint_lease",
            new=AsyncMock(return_value=None),
            create=True,
        ),
        patch(
            "app.control.account.sso_build._resolve_mint_profile",
            new=AsyncMock(
                return_value=ProxyProfile(
                    cf_cookies="cf_clearance=tc",
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/148.0.0.0 Safari/537.36"
                    ),
                    cf_clearance="tc",
                )
            ),
            create=True,
        ),
        patch(
            "app.control.account.sso_build._resolve_cf_clearance_value",
            new=AsyncMock(return_value="tc"),
            create=True,
        ),
        patch(
            "app.dataplane.proxy.adapters.session.build_session_kwargs",
            return_value={},
        ) as mock_build,
        pytest.raises(RuntimeError, match="network stopped"),
    ):
        await sso_build._mint_via_pkce_cs("sso-token")

    mock_build.assert_called_once()
    # No hardcoded impersonate reaches the session constructor —
    # impersonation now derives from the lease profile via build_session_kwargs.
    assert not any("chrome131" in str(c) for c in mock_session.call_args_list)


# ═══════════════════════════════════════════════════════════════════════════
# PKCE-CS reference-alignment tests (cpa_pkce_mint.py 12-defect audit)
# ═══════════════════════════════════════════════════════════════════════════

SETTER_URL = "https://accounts.x.ai/set-cookie/abc"
CONSENT_PAGE_URL = "https://accounts.x.ai/oauth2/consent?response_type=code"


@pytest.fixture
def pkce_env() -> Any:
    """Enter standard lease/profile patches for PKCE-CS mint tests (auto-exit)."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch(
            "app.control.account.sso_build._acquire_mint_lease",
            new=AsyncMock(return_value=None),
            create=True,
        )
    )
    stack.enter_context(
        patch(
            "app.control.account.sso_build._resolve_mint_profile",
            new=AsyncMock(
                return_value=ProxyProfile(cf_cookies="", user_agent="", cf_clearance="")
            ),
            create=True,
        )
    )
    stack.enter_context(
        patch(
            "app.dataplane.proxy.adapters.session.build_session_kwargs",
            return_value={},
        )
    )
    yield
    stack.close()


def _make_curl_resp(
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    text: str = "",
    url: str = "",
    json_data: dict[str, Any] | None = None,
) -> MagicMock:
    """Create a mock curl_cffi response (plain attributes, no async ctx)."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.text = text
    resp.url = url
    if json_data is not None:
        resp.json = MagicMock(return_value=json_data)
    return resp


def _pkce_flow_session(
    *,
    form_html: str,
    consent_post_resp: MagicMock,
    token_json: dict[str, Any],
) -> tuple[MagicMock, dict[str, Any]]:
    """Mock session for a full PKCE-CS consent flow.

    GET order: authorize → set-cookie redirect → consent page.
    POST order: CreateCookieSetterLink → consent form → token exchange.
    Returns (session, grpc_parse_result) — the caller patches
    _grpc_parse_response with the second value.
    """
    grpc_parse = {
        "grpc_status": 0,
        "messages": [[{"type": "string", "value": SETTER_URL}]],
        "trailers": {},
    }
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.get = AsyncMock(
        side_effect=[
            _make_curl_resp(),  # authorize
            _make_curl_resp(headers={"location": CONSENT_PAGE_URL}),  # set-cookie hop
            _make_curl_resp(text=form_html, url=CONSENT_PAGE_URL),  # consent page
        ]
    )
    session.post = AsyncMock(
        side_effect=[
            _make_curl_resp(),  # CreateCookieSetterLink
            consent_post_resp,
            _make_curl_resp(json_data=token_json),
        ]
    )
    return session, grpc_parse


class TestPKCEReferenceAlignment:
    """PKCE-CS alignment with cpa_pkce_mint.py — defect-by-defect coverage."""

    @pytest.mark.asyncio
    async def test_pkce_sets_cookie_on_all_four_domains(self, pkce_env: Any) -> None:
        """Defect 1: sso+sso-rw set on all four reference domains."""
        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = AsyncMock(side_effect=RuntimeError("stop after seeding"))
        with (
            patch("curl_cffi.requests.AsyncSession", return_value=mock_session),
            pytest.raises(RuntimeError, match="stop after seeding"),
        ):
            await sso_build._mint_via_pkce_cs("sso-token")

        domains: dict[str, set[str]] = {}
        for call in mock_session.cookies.set.call_args_list:
            domains.setdefault(call.args[0], set()).add(call.kwargs["domain"])
        expected = {"accounts.x.ai", ".accounts.x.ai", ".x.ai", "auth.x.ai"}
        assert set(domains["sso"]) == expected
        assert set(domains["sso-rw"]) == expected

    @pytest.mark.asyncio
    async def test_pkce_consent_form_regex_action_first_variant(
        self, pkce_env: Any
    ) -> None:
        """Defect 2: '<form action=... method="POST">' (action first) still submits."""
        state = "state1234567890abcdef"
        form_html = (
            '<form action="https://accounts.x.ai/oauth2/consent/allow" '
            'method="POST"><input name="csrf" value="tok123"/></form>'
        )
        post_resp = _make_curl_resp(
            headers={
                "location": f"http://127.0.0.1:56121/callback?code=code-b&state={state}"
            }
        )
        session, grpc_parse = _pkce_flow_session(
            form_html=form_html,
            consent_post_resp=post_resp,
            token_json={
                "access_token": "at-b",
                "refresh_token": "rt-b",
                "id_token": "",
                "expires_in": 21600,
            },
        )
        with (
            patch("curl_cffi.requests.AsyncSession", return_value=session),
            patch(
                "app.control.account.sso_build._grpc_parse_response",
                return_value=grpc_parse,
            ),
            patch(
                "app.control.account.sso_build.secrets.token_hex",
                side_effect=lambda _n: state,
            ),
        ):
            seed = await sso_build._mint_via_pkce_cs("sso-token")

        assert seed.access_token == "at-b"
        assert seed.refresh_token == "rt-b"

    @pytest.mark.asyncio
    async def test_pkce_extracts_bare_code_param(self, pkce_env: Any) -> None:
        """Defect 3: consent POST body with bare 'code=abc123XYZ._-~' yields code."""
        form_html = (
            '<form method="POST" action="https://accounts.x.ai/oauth2/consent/allow">'
            '<input name="csrf" value="t"/></form>'
        )
        post_resp = _make_curl_resp(text="ok code=abc123XYZ._-~ done")
        session, grpc_parse = _pkce_flow_session(
            form_html=form_html,
            consent_post_resp=post_resp,
            token_json={
                "access_token": "at-c",
                "refresh_token": "rt-c",
                "id_token": "",
                "expires_in": 21600,
            },
        )
        with (
            patch("curl_cffi.requests.AsyncSession", return_value=session),
            patch(
                "app.control.account.sso_build._grpc_parse_response",
                return_value=grpc_parse,
            ),
        ):
            seed = await sso_build._mint_via_pkce_cs("sso-token")

        assert seed.access_token == "at-c"

    @pytest.mark.asyncio
    async def test_pkce_consent_access_denied_raises(self, pkce_env: Any) -> None:
        """Defect 4: consent POST response mentioning access_denied raises."""
        form_html = (
            '<form method="POST" action="https://accounts.x.ai/oauth2/consent/allow">'
            '<input name="csrf" value="t"/></form>'
        )
        post_resp = _make_curl_resp(text="access_denied: user denied consent")
        session, grpc_parse = _pkce_flow_session(
            form_html=form_html,
            consent_post_resp=post_resp,
            token_json={"access_token": "unused", "refresh_token": "unused"},
        )
        with (
            patch("curl_cffi.requests.AsyncSession", return_value=session),
            patch(
                "app.control.account.sso_build._grpc_parse_response",
                return_value=grpc_parse,
            ),
            pytest.raises(RuntimeError, match="access_denied"),
        ):
            await sso_build._mint_via_pkce_cs("sso-token")

    @pytest.mark.asyncio
    async def test_pkce_grpc_headers_include_sec_fetch(self, pkce_env: Any) -> None:
        """Defect 5: CreateCookieSetterLink request carries sec-fetch-site/mode/dest."""
        form_html = (
            '<form method="POST" action="https://accounts.x.ai/oauth2/consent/allow">'
            '<input name="csrf" value="t"/></form>'
        )
        post_resp = _make_curl_resp(text="ok code=code-e")
        session, grpc_parse = _pkce_flow_session(
            form_html=form_html,
            consent_post_resp=post_resp,
            token_json={"access_token": "at-e", "refresh_token": "rt-e"},
        )
        with (
            patch("curl_cffi.requests.AsyncSession", return_value=session),
            patch(
                "app.control.account.sso_build._grpc_parse_response",
                return_value=grpc_parse,
            ),
        ):
            await sso_build._mint_via_pkce_cs("sso-token")

        grpc_call = next(
            c
            for c in session.post.call_args_list
            if c.args[0] == sso_build.CREATE_COOKIE_SETTER_RPC
        )
        headers = grpc_call.kwargs["headers"]
        assert headers["sec-fetch-site"] == "same-origin"
        assert headers["sec-fetch-mode"] == "cors"
        assert headers["sec-fetch-dest"] == "empty"

    @pytest.mark.asyncio
    async def test_pkce_consent_post_headers_complete(self, pkce_env: Any) -> None:
        """Defect 8: consent form POST carries origin/referer/sec-fetch×3."""
        form_html = (
            '<form method="POST" action="https://accounts.x.ai/oauth2/consent/allow">'
            '<input name="csrf" value="t"/></form>'
        )
        post_resp = _make_curl_resp(text="ok code=code-f")
        session, grpc_parse = _pkce_flow_session(
            form_html=form_html,
            consent_post_resp=post_resp,
            token_json={"access_token": "at-f", "refresh_token": "rt-f"},
        )
        with (
            patch("curl_cffi.requests.AsyncSession", return_value=session),
            patch(
                "app.control.account.sso_build._grpc_parse_response",
                return_value=grpc_parse,
            ),
        ):
            await sso_build._mint_via_pkce_cs("sso-token")

        consent_call = next(
            c
            for c in session.post.call_args_list
            if c.kwargs["headers"].get("content-type")
            == "application/x-www-form-urlencoded"
        )
        headers = consent_call.kwargs["headers"]
        assert headers["origin"] == sso_build.ACCOUNTS_ORIGIN
        assert headers["referer"] == CONSENT_PAGE_URL
        assert headers["sec-fetch-site"] == "same-origin"
        assert headers["sec-fetch-mode"] == "cors"
        assert headers["sec-fetch-dest"] == "empty"

    @pytest.mark.asyncio
    async def test_pkce_token_exchange_requires_refresh_token(
        self, pkce_env: Any
    ) -> None:
        """Defect 10: token response missing refresh_token raises."""
        form_html = (
            '<form method="POST" action="https://accounts.x.ai/oauth2/consent/allow">'
            '<input name="csrf" value="t"/></form>'
        )
        post_resp = _make_curl_resp(text="ok code=code-g")
        session, grpc_parse = _pkce_flow_session(
            form_html=form_html,
            consent_post_resp=post_resp,
            token_json={"access_token": "at-g", "id_token": "", "expires_in": 21600},
        )
        with (
            patch("curl_cffi.requests.AsyncSession", return_value=session),
            patch(
                "app.control.account.sso_build._grpc_parse_response",
                return_value=grpc_parse,
            ),
            pytest.raises(RuntimeError, match="access_token/refresh_token"),
        ):
            await sso_build._mint_via_pkce_cs("sso-token")

    @pytest.mark.asyncio
    async def test_pkce_consent_form_no_fields_raises(self, pkce_env: Any) -> None:
        """Defect 11: consent form with zero <input> fields raises."""
        form_html = (
            '<form action="https://accounts.x.ai/oauth2/consent/allow" method="POST">'
            "</form>"
        )
        session, grpc_parse = _pkce_flow_session(
            form_html=form_html,
            consent_post_resp=_make_curl_resp(text="unused"),
            token_json={"access_token": "unused", "refresh_token": "unused"},
        )
        with (
            patch("curl_cffi.requests.AsyncSession", return_value=session),
            patch(
                "app.control.account.sso_build._grpc_parse_response",
                return_value=grpc_parse,
            ),
            pytest.raises(RuntimeError, match="no input fields"),
        ):
            await sso_build._mint_via_pkce_cs("sso-token")

    @pytest.mark.asyncio
    async def test_pkce_grpc_status_header_precedes_body(self, pkce_env: Any) -> None:
        """Defect 6: grpc-status header wins over body trailers."""
        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = AsyncMock(return_value=_make_curl_resp())
        mock_session.post = AsyncMock(
            return_value=_make_curl_resp(
                headers={"grpc-status": "7", "grpc-message": "header boom"}
            )
        )
        with (
            patch("curl_cffi.requests.AsyncSession", return_value=mock_session),
            patch(
                "app.control.account.sso_build._grpc_parse_response",
                return_value={"grpc_status": 0, "messages": [], "trailers": {}},
            ),
            pytest.raises(RuntimeError, match="header boom"),
        ):
            await sso_build._mint_via_pkce_cs("sso-token")

    @pytest.mark.asyncio
    async def test_pkce_cookie_setter_chain_fail_fast(self, pkce_env: Any) -> None:
        """Defect 7: set-cookie chain not reaching consent/code raises immediately."""
        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = AsyncMock(
            side_effect=[
                _make_curl_resp(),  # authorize
                _make_curl_resp(),  # set-cookie hop: 200, no location, empty body
            ]
        )
        mock_session.post = AsyncMock(return_value=_make_curl_resp())
        with (
            patch("curl_cffi.requests.AsyncSession", return_value=mock_session),
            patch(
                "app.control.account.sso_build._grpc_parse_response",
                return_value={
                    "grpc_status": 0,
                    "messages": [[{"type": "string", "value": SETTER_URL}]],
                    "trailers": {},
                },
            ),
            pytest.raises(RuntimeError, match="did not reach consent"),
        ):
            await sso_build._mint_via_pkce_cs("sso-token")

        assert mock_session.get.await_count == 2

    @pytest.mark.asyncio
    async def test_pkce_chain_code_with_127_0_0_1_url(self, pkce_env: Any) -> None:
        """Defect 9: code in a 127.0.0.1 redirect URL (non-canonical port) is picked up."""
        state = "state1234567890abcdef"
        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = AsyncMock(
            side_effect=[
                _make_curl_resp(),  # authorize
                _make_curl_resp(  # set-cookie hop → 127.0.0.1:9999 callback
                    headers={
                        "location": (
                            f"http://127.0.0.1:9999/callback?code=code-9&state={state}"
                        )
                    }
                ),
            ]
        )
        mock_session.post = AsyncMock(
            side_effect=[
                _make_curl_resp(),  # CreateCookieSetterLink
                _make_curl_resp(  # token exchange
                    json_data={
                        "access_token": "at-9",
                        "refresh_token": "rt-9",
                        "id_token": "",
                        "expires_in": 21600,
                    }
                ),
            ]
        )
        with (
            patch("curl_cffi.requests.AsyncSession", return_value=mock_session),
            patch(
                "app.control.account.sso_build._grpc_parse_response",
                return_value={
                    "grpc_status": 0,
                    "messages": [[{"type": "string", "value": SETTER_URL}]],
                    "trailers": {},
                },
            ),
            patch(
                "app.control.account.sso_build.secrets.token_hex",
                side_effect=lambda _n: state,
            ),
        ):
            seed = await sso_build._mint_via_pkce_cs("sso-token")

        assert seed.access_token == "at-9"
