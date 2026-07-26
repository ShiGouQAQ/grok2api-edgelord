"""Tests for SSO→Build credential conversion."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.control.account.sso_build import convert_sso_to_build


def _make_ctx_resp(data: dict) -> MagicMock:
    """Create a mock aiohttp response context manager that returns JSON.

    aiohttp's session.post() is synchronous — it returns an async context manager,
    NOT a coroutine. So the mock must be a plain MagicMock with async __aenter__/__aexit__.
    """
    resp = MagicMock()
    resp.json = AsyncMock(return_value=data)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_session(responses: list) -> MagicMock:
    """Create a mock session whose post() returns context managers in order."""
    session = MagicMock()
    session.post = MagicMock(side_effect=responses)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


@pytest.mark.asyncio
async def test_convert_sso_to_build_invalid_token():
    """Invalid SSO token → device flow succeeds, token poll returns access_denied."""
    device_resp = _make_ctx_resp({"device_code": "dc"})
    denied_resp = _make_ctx_resp({"error": "access_denied"})

    with patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls:
        mock_cls.return_value = _make_session([device_resp, denied_resp])

        with pytest.raises(PermissionError, match="denied"):
            await convert_sso_to_build("invalid-token")


@pytest.mark.asyncio
async def test_convert_sso_to_build_success():
    """Valid SSO token → returns credentials dict."""
    device_resp = _make_ctx_resp({"device_code": "dc-123"})
    token_resp = _make_ctx_resp(
        {
            "access_token": "at-ok",
            "refresh_token": "rt-ok",
            "id_token": "id-ok",
            "expires_in": 7200,
        }
    )

    with patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls:
        mock_cls.return_value = _make_session([device_resp, token_resp])

        result = await convert_sso_to_build("good-sso")

    assert result["access_token"] == "at-ok"
    assert result["refresh_token"] == "rt-ok"
    assert result["id_token"] == "id-ok"
    assert result["expires_in"] == "7200"


@pytest.mark.asyncio
async def test_convert_sso_to_build_timeout():
    """Polling never resolves → TimeoutError."""
    device_resp = _make_ctx_resp({"device_code": "dc"})
    pending_resp = _make_ctx_resp({"error": "authorization_pending"})

    with patch("app.control.account.sso_build.aiohttp.ClientSession") as mock_cls:
        mock_cls.return_value = _make_session([device_resp] + [pending_resp] * 60)

        with patch(
            "app.control.account.sso_build.asyncio.sleep", new_callable=AsyncMock
        ):
            with pytest.raises(TimeoutError, match="timed out"):
                await convert_sso_to_build("sso-token")
