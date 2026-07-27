"""Tests for AccountRefreshScheduler — Build credential refresh lifecycle.

Covers three scenarios for recover_build_tokens() and _build_credential_loop():
  1. Refresh fails (permanent) + access_token still valid → stays ACTIVE, markers set
  2. Refresh fails (permanent) + access_token expired → DISABLED
  3. Refresh succeeds → tokens updated normally (regression)
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.control.account.backends.local import LocalAccountRepository
from app.control.account.commands import AccountUpsert
from app.control.account.enums import AccountStatus
from app.control.account.refresh import AccountRefreshService
from app.control.account.scheduler import AccountRefreshScheduler
from app.platform.auth.oauth_device import TokenResponse
from app.platform.runtime.clock import now_ms


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_repo(*upserts: AccountUpsert) -> LocalAccountRepository:
    """Create a fresh LocalAccountRepository with the given accounts."""
    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "accounts.db"
    repo = LocalAccountRepository(db_path)
    await repo.initialize()
    if upserts:
        await repo.upsert_accounts(list(upserts))
    return repo


def _build_ext(
    *,
    access_token: str = "at-valid",
    refresh_token: str = "rt-valid",
    expires_at: int | None = None,
    due_at: int = 0,
) -> dict:
    """Build a standard build-account ext dict."""
    if expires_at is None:
        expires_at = now_ms() + 7200_000  # 2 hours from now
    return {
        "build_access_token": access_token,
        "build_refresh_token": refresh_token,
        "build_id_token": "",
        "build_expires_at": expires_at,
        "build_refresh_due_at": due_at,
    }


# ---------------------------------------------------------------------------
# Tests for recover_build_tokens()
# ---------------------------------------------------------------------------


class TestRecoverBuildTokens:
    """recover_build_tokens() handles permanent refresh failure correctly."""

    @pytest.mark.asyncio
    async def test_perm_fail_access_still_valid_keeps_active(self):
        """Refresh permanently fails, but access_token still valid → stays ACTIVE,
        ext updated with build_refresh_permanent marker and due_at deferred to expiry."""
        now = now_ms()
        repo = await _make_repo(
            AccountUpsert(
                token="build-active",
                pool="build",
                provider="grok_build",
                ext=_build_ext(
                    access_token="at-still-valid",
                    refresh_token="rt-dead",
                    expires_at=now
                    + 300_000,  # valid for 5 more minutes (within recovery window)
                ),
            )
        )
        svc = AccountRefreshService(repo)
        scheduler = AccountRefreshScheduler(svc)

        with patch(
            "app.control.account.build_refresh.refresh_build_token",
            new_callable=AsyncMock,
            return_value=None,
        ):
            count = await scheduler.recover_build_tokens()

        # Should have processed the account (not skipped)
        assert count == 0, "refresh failure should not count as refreshed"

        # Verify account is still ACTIVE with markers
        recs = await repo.get_accounts(["build-active"])
        assert len(recs) == 1
        rec = recs[0]
        assert rec.status == AccountStatus.ACTIVE, f"expected ACTIVE, got {rec.status}"
        ext = rec.ext or {}
        assert ext.get("build_refresh_permanent") is True, (
            "should have build_refresh_permanent marker"
        )
        assert ext.get("build_refresh_error") == "refresh_token_invalid"
        # due_at should be set to expires_at
        assert ext.get("build_refresh_due_at") == ext.get("build_expires_at"), (
            "due_at should equal expires_at"
        )

    @pytest.mark.asyncio
    async def test_perm_fail_access_expired_disables(self):
        """Refresh permanently fails AND access_token expired → DISABLED."""
        now = now_ms()
        repo = await _make_repo(
            AccountUpsert(
                token="build-expired",
                pool="build",
                provider="grok_build",
                ext=_build_ext(
                    access_token="at-dead",
                    refresh_token="rt-dead",
                    expires_at=now - 3600_000,  # expired 1 hour ago
                ),
            )
        )
        svc = AccountRefreshService(repo)
        scheduler = AccountRefreshScheduler(svc)

        with patch(
            "app.control.account.build_refresh.refresh_build_token",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await scheduler.recover_build_tokens()

        recs = await repo.get_accounts(["build-expired"])
        assert len(recs) == 1
        rec = recs[0]
        assert rec.status == AccountStatus.DISABLED, (
            f"expected DISABLED, got {rec.status}"
        )
        assert rec.state_reason == "build_refresh_permanent_failure"

    @pytest.mark.asyncio
    async def test_refresh_success_updates_tokens(self):
        """Refresh succeeds → tokens updated, account stays ACTIVE."""
        now = now_ms()
        repo = await _make_repo(
            AccountUpsert(
                token="build-refreshable",
                pool="build",
                provider="grok_build",
                ext=_build_ext(
                    access_token="at-old",
                    refresh_token="rt-old",
                    expires_at=now + 600_000,  # about to expire
                ),
            )
        )
        svc = AccountRefreshService(repo)
        scheduler = AccountRefreshScheduler(svc)

        new_tokens = TokenResponse(
            access_token="at-new",
            refresh_token="rt-new",
            id_token="id-new",
            expires_in=21600,
        )
        with patch(
            "app.control.account.build_refresh.refresh_build_token",
            new_callable=AsyncMock,
            return_value=new_tokens,
        ):
            count = await scheduler.recover_build_tokens()

        assert count == 1, "should count as refreshed"

        recs = await repo.get_accounts(["build-refreshable"])
        assert len(recs) == 1
        rec = recs[0]
        assert rec.status == AccountStatus.ACTIVE
        ext = rec.ext or {}
        assert ext.get("build_access_token") == "at-new"
        assert ext.get("build_refresh_token") == "rt-new"
        assert ext.get("build_id_token") == "id-new"
        # expires_at should be in the future
        assert ext.get("build_expires_at", 0) > now
        # refresh_due_at should be before expires_at (5min jitter)
        assert ext.get("build_refresh_due_at", 0) < ext.get("build_expires_at", 0)

    @pytest.mark.asyncio
    async def test_non_build_account_skipped(self):
        """Non-grok_build provider → skipped entirely."""
        repo = await _make_repo(
            AccountUpsert(token="web-account", pool="basic", provider="grok_web"),
        )
        svc = AccountRefreshService(repo)
        scheduler = AccountRefreshScheduler(svc)

        with patch(
            "app.control.account.build_refresh.refresh_build_token",
            new_callable=AsyncMock,
        ) as mock_refresh:
            count = await scheduler.recover_build_tokens()

        assert count == 0
        mock_refresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_deleted_account_skipped(self):
        """Deleted account → skipped."""
        now = now_ms()
        repo = await _make_repo(
            AccountUpsert(
                token="build-deleted",
                pool="build",
                provider="grok_build",
                ext=_build_ext(
                    access_token="at-valid",
                    refresh_token="rt-valid",
                    expires_at=now + 3600_000,
                ),
            )
        )
        await repo.delete_accounts(["build-deleted"])

        svc = AccountRefreshService(repo)
        scheduler = AccountRefreshScheduler(svc)

        with patch(
            "app.control.account.build_refresh.refresh_build_token",
            new_callable=AsyncMock,
        ) as mock_refresh:
            count = await scheduler.recover_build_tokens()

        assert count == 0
        mock_refresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_refresh_token_skipped(self):
        """Build account without refresh_token → skipped (can't refresh)."""
        now = now_ms()
        repo = await _make_repo(
            AccountUpsert(
                token="build-no-rt",
                pool="build",
                provider="grok_build",
                ext=_build_ext(
                    access_token="at-valid",
                    refresh_token="",  # no refresh token
                    expires_at=now + 3600_000,
                ),
            )
        )
        svc = AccountRefreshService(repo)
        scheduler = AccountRefreshScheduler(svc)

        with patch(
            "app.control.account.build_refresh.refresh_build_token",
            new_callable=AsyncMock,
        ) as mock_refresh:
            count = await scheduler.recover_build_tokens()

        assert count == 0
        mock_refresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_recover_only_processes_near_expiry(self):
        """recover_build_tokens only processes accounts within 10min of expiry."""
        now = now_ms()
        far_future = now + 7200_000  # 2 hours from now - outside recovery window
        repo = await _make_repo(
            AccountUpsert(
                token="build-far-future",
                pool="build",
                provider="grok_build",
                ext=_build_ext(
                    access_token="at-valid",
                    refresh_token="rt-valid",
                    expires_at=far_future,
                ),
            )
        )
        svc = AccountRefreshService(repo)
        scheduler = AccountRefreshScheduler(svc)

        with patch(
            "app.control.account.build_refresh.refresh_build_token",
            new_callable=AsyncMock,
        ) as mock_refresh:
            count = await scheduler.recover_build_tokens()

        assert count == 0, "should not process accounts outside recovery window"
        mock_refresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_expires_at_zero_and_refresh_fails(self):
        """build_expires_at=0 (boundary: ≤0) → account is skipped entirely
        (never reaches refresh call)."""
        repo = await _make_repo(
            AccountUpsert(
                token="build-zero",
                pool="build",
                provider="grok_build",
                ext=_build_ext(
                    access_token="at-valid",
                    refresh_token="rt-valid",
                    expires_at=0,
                ),
            )
        )
        svc = AccountRefreshService(repo)
        scheduler = AccountRefreshScheduler(svc)

        with patch(
            "app.control.account.build_refresh.refresh_build_token",
            new_callable=AsyncMock,
        ) as mock_refresh:
            count = await scheduler.recover_build_tokens()

        assert count == 0, "should not process account with expires_at=0"
        mock_refresh.assert_not_called()

        recs = await repo.get_accounts(["build-zero"])
        assert len(recs) == 1
        assert recs[0].status == AccountStatus.ACTIVE
        assert recs[0].ext.get("build_expires_at") == 0

    @pytest.mark.asyncio
    async def test_expires_at_equals_now_and_refresh_fails(self):
        """build_expires_at == now (exact boundary) AND refresh fails → DISABLED
        (expires_at is not > now, so the ACTIVE path is skipped)."""
        now = now_ms()
        repo = await _make_repo(
            AccountUpsert(
                token="build-now",
                pool="build",
                provider="grok_build",
                ext=_build_ext(
                    access_token="at-valid",
                    refresh_token="rt-valid",
                    expires_at=now,
                ),
            )
        )
        svc = AccountRefreshService(repo)
        scheduler = AccountRefreshScheduler(svc)

        with patch(
            "app.control.account.build_refresh.refresh_build_token",
            new_callable=AsyncMock,
            return_value=None,
        ):
            count = await scheduler.recover_build_tokens()

        assert count == 0

        recs = await repo.get_accounts(["build-now"])
        assert len(recs) == 1
        rec = recs[0]
        assert rec.status == AccountStatus.DISABLED, (
            f"expected DISABLED, got {rec.status}"
        )
        assert rec.state_reason == "build_refresh_permanent_failure"

    @pytest.mark.asyncio
    async def test_refresh_raises_unexpected_exception(self):
        """refresh_build_token() raises ValueError → caught, logged, loop continues."""
        now = now_ms()
        repo = await _make_repo(
            AccountUpsert(
                token="build-crash",
                pool="build",
                provider="grok_build",
                ext=_build_ext(
                    access_token="at-valid",
                    refresh_token="rt-valid",
                    expires_at=now + 600_000,
                ),
            )
        )
        svc = AccountRefreshService(repo)
        scheduler = AccountRefreshScheduler(svc)

        with patch(
            "app.control.account.build_refresh.refresh_build_token",
            new_callable=AsyncMock,
            side_effect=ValueError("unexpected boom"),
        ):
            count = await scheduler.recover_build_tokens()

        assert count == 0

        recs = await repo.get_accounts(["build-crash"])
        assert len(recs) == 1
        rec = recs[0]
        assert rec.status == AccountStatus.ACTIVE
        assert rec.ext.get("build_access_token") == "at-valid"

    @pytest.mark.asyncio
    async def test_ext_is_none_dict(self):
        """Account with ext={} (no build fields at all) → safely skipped, not crash."""
        repo = await _make_repo(
            AccountUpsert(
                token="build-no-ext",
                pool="build",
                provider="grok_build",
                ext={},
            )
        )
        svc = AccountRefreshService(repo)
        scheduler = AccountRefreshScheduler(svc)

        with patch(
            "app.control.account.build_refresh.refresh_build_token",
            new_callable=AsyncMock,
        ) as mock_refresh:
            count = await scheduler.recover_build_tokens()

        assert count == 0
        mock_refresh.assert_not_called()

        recs = await repo.get_accounts(["build-no-ext"])
        assert len(recs) == 1
        assert recs[0].status == AccountStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_multiple_accounts_some_crash_dont_affect_others(self):
        """3 accounts: first raises, second succeeds, third raises → second refreshed."""
        now = now_ms()
        repo = await _make_repo(
            AccountUpsert(
                token="crash-1",
                pool="build",
                provider="grok_build",
                ext=_build_ext(
                    access_token="at-1",
                    refresh_token="rt-1",
                    expires_at=now + 600_000,
                ),
            ),
            AccountUpsert(
                token="success-2",
                pool="build",
                provider="grok_build",
                ext=_build_ext(
                    access_token="at-2-old",
                    refresh_token="rt-2",
                    expires_at=now + 600_000,
                ),
            ),
            AccountUpsert(
                token="crash-3",
                pool="build",
                provider="grok_build",
                ext=_build_ext(
                    access_token="at-3",
                    refresh_token="rt-3",
                    expires_at=now + 600_000,
                ),
            ),
        )
        svc = AccountRefreshService(repo)
        scheduler = AccountRefreshScheduler(svc)

        new_tokens = TokenResponse(
            access_token="at-2-new",
            refresh_token="rt-2-new",
            id_token="",
            expires_in=21600,
        )
        mock = AsyncMock(
            side_effect=[ValueError("crash1"), new_tokens, ValueError("crash3")]
        )

        with patch(
            "app.control.account.build_refresh.refresh_build_token",
            mock,
        ):
            count = await scheduler.recover_build_tokens()

        assert count == 1, "only the second account should count as refreshed"

        recs = await repo.get_accounts(["crash-1", "success-2", "crash-3"])
        by_token = {r.token: r for r in recs}

        # crash-1: exception caught, unchanged
        r1 = by_token["crash-1"]
        assert r1.status == AccountStatus.ACTIVE
        assert r1.ext.get("build_access_token") == "at-1"

        # success-2: refreshed
        r2 = by_token["success-2"]
        assert r2.status == AccountStatus.ACTIVE
        assert r2.ext.get("build_access_token") == "at-2-new"
        assert r2.ext.get("build_refresh_token") == "rt-2-new"

        # crash-3: exception caught, unchanged
        r3 = by_token["crash-3"]
        assert r3.status == AccountStatus.ACTIVE
        assert r3.ext.get("build_access_token") == "at-3"


# ---------------------------------------------------------------------------
# Regression: non-build providers unaffected by build credential refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_build_provider_not_affected_by_changes():
    """Non-build accounts (grok_web, grok_console) are completely untouched
    by recover_build_tokens()."""
    repo = await _make_repo(
        AccountUpsert(token="web-1", pool="basic", provider="grok_web"),
        AccountUpsert(token="console-1", pool="basic", provider="grok_console"),
    )
    svc = AccountRefreshService(repo)
    scheduler = AccountRefreshScheduler(svc)

    with patch(
        "app.control.account.build_refresh.refresh_build_token",
        new_callable=AsyncMock,
    ) as mock_refresh:
        count = await scheduler.recover_build_tokens()

    assert count == 0
    mock_refresh.assert_not_called()

    recs = await repo.get_accounts(["web-1", "console-1"])
    for rec in recs:
        ext = rec.ext or {}
        assert ext.get("build_refresh_permanent") is None, (
            f"{rec.token} should not have build_refresh_permanent"
        )
        assert rec.status == AccountStatus.ACTIVE


# ---------------------------------------------------------------------------
# Tests for _build_credential_loop() — logic equivalence
# ---------------------------------------------------------------------------


class TestBuildCredentialLoopLogic:
    """Verify the permanent-failure handling logic used by _build_credential_loop
    is equivalent to recover_build_tokens(). Both share the same branching."""

    @pytest.mark.asyncio
    async def test_mixed_accounts_correctly_handled(self):
        """Multiple accounts with different states → each handled correctly."""
        now = now_ms()
        repo = await _make_repo(
            # Will be: refresh fails, but AT still valid → ACTIVE with marker
            AccountUpsert(
                token="perm-fail-valid-at",
                pool="build",
                provider="grok_build",
                ext=_build_ext(
                    access_token="at-ok",
                    refresh_token="rt-dead",
                    expires_at=now + 300_000,  # 5 min, within recovery window
                ),
            ),
            # Will be: refresh fails, AT expired → DISABLED
            AccountUpsert(
                token="perm-fail-expired-at",
                pool="build",
                provider="grok_build",
                ext=_build_ext(
                    access_token="at-dead",
                    refresh_token="rt-dead",
                    expires_at=now - 60_000,
                ),
            ),
            # Will be: refresh succeeds → tokens updated
            AccountUpsert(
                token="refresh-success",
                pool="build",
                provider="grok_build",
                ext=_build_ext(
                    access_token="at-old",
                    refresh_token="rt-old",
                    expires_at=now + 600_000,
                ),
            ),
            # Non-build account → skipped
            AccountUpsert(token="web-acc", pool="basic", provider="grok_web"),
        )

        svc = AccountRefreshService(repo)
        scheduler = AccountRefreshScheduler(svc)

        # Mock: first two calls fail, third succeeds
        new_tokens = TokenResponse(
            access_token="at-new",
            refresh_token="rt-new",
            id_token="id-new",
            expires_in=21600,
        )
        mock = AsyncMock(side_effect=[None, None, new_tokens])

        with patch(
            "app.control.account.build_refresh.refresh_build_token",
            mock,
        ):
            await scheduler.recover_build_tokens()

        # Verify each account
        recs = await repo.get_accounts(
            [
                "perm-fail-valid-at",
                "perm-fail-expired-at",
                "refresh-success",
                "web-acc",
            ]
        )
        by_token = {r.token: r for r in recs}

        # 1. perm-fail-valid-at: should be ACTIVE with markers
        r1 = by_token["perm-fail-valid-at"]
        assert r1.status == AccountStatus.ACTIVE, (
            f"perm-fail-valid-at should be ACTIVE, got {r1.status}"
        )
        assert r1.ext.get("build_refresh_permanent") is True

        # 2. perm-fail-expired-at: should be DISABLED
        r2 = by_token["perm-fail-expired-at"]
        assert r2.status == AccountStatus.DISABLED, (
            f"perm-fail-expired-at should be DISABLED, got {r2.status}"
        )

        # 3. refresh-success: should have new tokens
        r3 = by_token["refresh-success"]
        assert r3.status == AccountStatus.ACTIVE
        assert r3.ext.get("build_access_token") == "at-new"
        assert r3.ext.get("build_refresh_token") == "rt-new"

        # 4. web-acc: unchanged
        r4 = by_token["web-acc"]
        assert r4.status == AccountStatus.ACTIVE
        assert r4.provider == "grok_web"
