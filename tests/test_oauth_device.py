"""Tests for app.platform.auth.oauth_device — OAuth Device Flow client."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.platform.auth.oauth_device import (
    AccessDenied,
    AuthorizationPending,
    DeviceCodeResponse,
    DeviceFlowClient,
    ExpiredToken,
    SlowDown,
    TokenResponse,
)


# ─── Data class tests ───────────────────────────────────────────────────────


class TestDeviceCodeResponseModel:
    """Verify DeviceCodeResponse dataclass defaults and field access."""

    def test_create_with_defaults(self):
        resp = DeviceCodeResponse(
            device_code="dc-123",
            user_code="ABCD-EFGH",
            verification_uri="https://auth.x.ai/activate",
        )
        assert resp.device_code == "dc-123"
        assert resp.user_code == "ABCD-EFGH"
        assert resp.verification_uri == "https://auth.x.ai/activate"
        assert resp.verification_uri_complete == ""
        assert resp.interval == 5
        assert resp.expires_in == 1800

    def test_create_with_all_fields(self):
        resp = DeviceCodeResponse(
            device_code="dc-456",
            user_code="WXYZ-1234",
            verification_uri="https://auth.x.ai/activate",
            verification_uri_complete="https://auth.x.ai/activate?user_code=WXYZ-1234",
            interval=10,
            expires_in=600,
        )
        assert resp.interval == 10
        assert resp.expires_in == 600
        assert "user_code=WXYZ-1234" in resp.verification_uri_complete


class TestTokenResponseModel:
    """Verify TokenResponse dataclass defaults and field access."""

    def test_create_with_defaults(self):
        resp = TokenResponse(access_token="at-abc")
        assert resp.access_token == "at-abc"
        assert resp.refresh_token == ""
        assert resp.id_token == ""
        assert resp.expires_in == 3600

    def test_create_with_all_fields(self):
        resp = TokenResponse(
            access_token="at-xyz",
            refresh_token="rt-xyz",
            id_token="id-xyz",
            expires_in=7200,
        )
        assert resp.refresh_token == "rt-xyz"
        assert resp.id_token == "id-xyz"
        assert resp.expires_in == 7200


# ─── Helper: mock aiohttp response ─────────────────────────────────────────


def _make_mock_response(status: int, payload: dict) -> MagicMock:
    """Create a mock aiohttp ClientResponse that returns *payload* as JSON."""
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=payload)
    resp.raise_for_status = MagicMock()
    resp.request_info = MagicMock()
    resp.history = ()
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_mock_session(status: int, payload: dict) -> MagicMock:
    """Create a mock aiohttp ClientSession whose request returns *payload*."""
    mock_resp = _make_mock_response(status, payload)
    session = MagicMock()
    session.request = MagicMock(return_value=mock_resp)
    session.closed = False
    session.close = AsyncMock()
    return session


# ─── start_device tests ─────────────────────────────────────────────────────


class TestStartDevice:
    """Verify start_device sends correct params and parses the response."""

    @pytest.mark.asyncio
    async def test_returns_correct_type(self):
        payload = {
            "device_code": "dc-test",
            "user_code": "TEST-CODE",
            "verification_uri": "https://auth.x.ai/activate",
            "verification_uri_complete": "https://auth.x.ai/activate?user_code=TEST-CODE",
            "interval": 5,
            "expires_in": 1800,
        }
        session = _make_mock_session(200, payload)
        client = DeviceFlowClient(session=session)

        result = await client.start_device(referrer="grok-build")

        assert isinstance(result, DeviceCodeResponse)
        assert result.device_code == "dc-test"
        assert result.user_code == "TEST-CODE"
        # Verify correct URL was called
        session.request.assert_called_once()
        call_args = session.request.call_args
        assert call_args[0][0] == "POST"
        assert call_args[0][1] == DeviceFlowClient.DEVICE_URL


# ─── poll_device tests ──────────────────────────────────────────────────────


class TestPollDevice:
    """Verify poll_device parses tokens and raises on error responses."""

    @pytest.mark.asyncio
    async def test_pending_raises_authorization_pending(self):
        payload = {"error": "authorization_pending"}
        session = _make_mock_session(400, payload)
        client = DeviceFlowClient(session=session)

        with pytest.raises(AuthorizationPending):
            await client.poll_device("dc-abc")

    @pytest.mark.asyncio
    async def test_access_denied_raises(self):
        payload = {"error": "access_denied"}
        session = _make_mock_session(400, payload)
        client = DeviceFlowClient(session=session)

        with pytest.raises(AccessDenied):
            await client.poll_device("dc-abc")

    @pytest.mark.asyncio
    async def test_slow_down_raises(self):
        payload = {"error": "slow_down"}
        session = _make_mock_session(400, payload)
        client = DeviceFlowClient(session=session)

        with pytest.raises(SlowDown):
            await client.poll_device("dc-abc")

    @pytest.mark.asyncio
    async def test_expired_token_raises(self):
        payload = {"error": "expired_token"}
        session = _make_mock_session(400, payload)
        client = DeviceFlowClient(session=session)

        with pytest.raises(ExpiredToken):
            await client.poll_device("dc-abc")


# ─── refresh_token tests ────────────────────────────────────────────────────


class TestRefreshToken:
    """Verify refresh_token sends correct grant_type and parses response."""

    @pytest.mark.asyncio
    async def test_success(self):
        payload = {
            "access_token": "new-at",
            "refresh_token": "new-rt",
            "id_token": "new-id",
            "expires_in": 7200,
        }
        session = _make_mock_session(200, payload)
        client = DeviceFlowClient(session=session)

        result = await client.refresh_token("old-rt")

        assert isinstance(result, TokenResponse)
        assert result.access_token == "new-at"
        assert result.refresh_token == "new-rt"
        assert result.id_token == "new-id"
        assert result.expires_in == 7200

        # Verify correct form data
        call_data = (
            session.request.call_args[1].get("data") or session.request.call_args[0][2]
            if len(session.request.call_args[0]) > 2
            else session.request.call_args[1]["data"]
        )
        assert call_data["grant_type"] == "refresh_token"
        assert call_data["refresh_token"] == "old-rt"


# ─── poll_with_retry tests ──────────────────────────────────────────────────


class TestPollWithRetry:
    """Verify poll_with_retry handles pending/slowdown and succeeds."""

    @pytest.mark.asyncio
    async def test_succeeds_after_pending(self):
        """First call returns pending, second returns token."""
        pending_payload = {"error": "authorization_pending"}
        token_payload = {
            "access_token": "final-at",
            "refresh_token": "",
            "id_token": "",
            "expires_in": 3600,
        }
        pending_resp = _make_mock_response(400, pending_payload)
        token_resp = _make_mock_response(200, token_payload)

        session = MagicMock()
        session.request = MagicMock(side_effect=[pending_resp, token_resp])
        session.closed = False
        session.close = AsyncMock()
        client = DeviceFlowClient(session=session)

        with patch(
            "app.platform.auth.oauth_device.asyncio.sleep", new_callable=AsyncMock
        ):
            result = await client.poll_with_retry("dc-abc", interval=1, timeout=10)

        assert isinstance(result, TokenResponse)
        assert result.access_token == "final-at"
        assert session.request.call_count == 2

    @pytest.mark.asyncio
    async def test_timeout_raises(self):
        """All calls return pending until timeout."""
        pending_payload = {"error": "authorization_pending"}
        pending_resp = _make_mock_response(400, pending_payload)

        session = MagicMock()
        session.request = MagicMock(return_value=pending_resp)
        session.closed = False
        session.close = AsyncMock()
        client = DeviceFlowClient(session=session)

        with patch(
            "app.platform.auth.oauth_device.asyncio.sleep", new_callable=AsyncMock
        ):
            with pytest.raises(TimeoutError, match="timed out"):
                await client.poll_with_retry("dc-abc", interval=1, timeout=3)

    @pytest.mark.asyncio
    async def test_slow_down_increases_interval(self):
        """SlowDown responses should increase interval by 2s."""
        slow_resp = _make_mock_response(400, {"error": "slow_down"})
        token_resp = _make_mock_response(
            200,
            {
                "access_token": "at",
                "refresh_token": "",
                "id_token": "",
                "expires_in": 3600,
            },
        )

        session = MagicMock()
        session.request = MagicMock(side_effect=[slow_resp, token_resp])
        session.closed = False
        session.close = AsyncMock()
        client = DeviceFlowClient(session=session)

        sleep_mock = AsyncMock()
        with patch("app.platform.auth.oauth_device.asyncio.sleep", sleep_mock):
            result = await client.poll_with_retry("dc-abc", interval=5, timeout=60)

        assert result.access_token == "at"
        # slow_down from 5 → 7, so sleep should be called with 7
        sleep_mock.assert_called_once_with(7)
