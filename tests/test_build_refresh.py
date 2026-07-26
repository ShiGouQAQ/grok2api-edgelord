"""Tests for build_refresh — OAuth token refresh scheduling."""

import pytest

from app.control.account.build_refresh import (
    compute_refresh_due_at,
    refresh_build_token,
)


class TestComputeRefreshDueAt:
    def test_before_expiry(self):
        due = compute_refresh_due_at(1000.0, "test-account")
        assert due < 1000.0

    def test_within_bounds(self):
        due = compute_refresh_due_at(1000.0, "test-account")
        # expires_at - 300 (5min) - up to 180s jitter
        assert due > 1000.0 - 500.0

    def test_deterministic(self):
        due1 = compute_refresh_due_at(1000.0, "test-account")
        due2 = compute_refresh_due_at(1000.0, "test-account")
        assert due1 == due2

    def test_different_accounts(self):
        due1 = compute_refresh_due_at(1000.0, "account-a")
        due2 = compute_refresh_due_at(1000.0, "account-b")
        assert due1 != due2


class TestRefreshBuildToken:
    @pytest.mark.asyncio
    async def test_access_denied_returns_none(self):
        from unittest.mock import AsyncMock, patch
        from app.platform.auth.oauth_device import AccessDenied

        client = AsyncMock()
        client.refresh_token = AsyncMock(side_effect=AccessDenied())
        result = await refresh_build_token("some-token", client=client)
        assert result is None

    @pytest.mark.asyncio
    async def test_expired_token_returns_none(self):
        from unittest.mock import AsyncMock
        from app.platform.auth.oauth_device import ExpiredToken

        client = AsyncMock()
        client.refresh_token = AsyncMock(side_effect=ExpiredToken())
        result = await refresh_build_token("some-token", client=client)
        assert result is None

    @pytest.mark.asyncio
    async def test_success_returns_token(self):
        from unittest.mock import AsyncMock
        from app.platform.auth.oauth_device import TokenResponse

        expected = TokenResponse(access_token="new-access", refresh_token="new-refresh")
        client = AsyncMock()
        client.refresh_token = AsyncMock(return_value=expected)
        result = await refresh_build_token("some-token", client=client)
        assert result is expected
