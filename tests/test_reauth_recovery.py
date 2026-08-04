"""REAUTH_REQUIRED account recovery path.

A reauth-marked account leaves the selection pool but stays recoverable:
- refresh success → restored to ACTIVE (state_reason cleared, reauth ext keys
  removed) — aligns with Go: refresh candidates include reauthRequired and a
  successful quota sync restores the account.
- admin clear_failures → reauth_at/reauth_reason keys wiped across backends.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.control.account.backends.local import LocalAccountRepository
from app.control.account.commands import AccountPatch, AccountUpsert
from app.control.account.enums import AccountStatus, QuotaSource
from app.control.account.invalid_credentials import mark_account_reauth_required
from app.control.account.models import QuotaWindow
from app.control.account.refresh import AccountRefreshService
from app.control.account.state_machine import is_manageable

REAUTH_TOKEN = "reauth-tok"
REAUTH_REASON = "reauth: upstream 401"


async def _make_repo(tmp_path, *upserts: AccountUpsert) -> LocalAccountRepository:
    """Fresh LocalAccountRepository with the given accounts (tmp sqlite)."""
    repo = LocalAccountRepository(tmp_path / "accounts.db")
    await repo.initialize()
    if upserts:
        await repo.upsert_accounts(list(upserts))
    return repo


async def _reauth_repo(tmp_path) -> LocalAccountRepository:
    """Repo with a single basic account already marked REAUTH_REQUIRED."""
    repo = await _make_repo(
        tmp_path,
        AccountUpsert(token=REAUTH_TOKEN, pool="basic", provider="grok_web"),
    )
    await mark_account_reauth_required(repo, REAUTH_TOKEN, REAUTH_REASON, source="test")
    record = (await repo.get_accounts([REAUTH_TOKEN]))[0]
    assert record.status == AccountStatus.REAUTH_REQUIRED
    assert record.ext.get("reauth_at") is not None
    assert record.ext.get("reauth_reason") == REAUTH_REASON
    return repo


def _fast_window() -> QuotaWindow:
    """A live fast-mode quota window (basic pool, mode 1)."""
    return QuotaWindow(
        remaining=2,
        total=2,
        window_seconds=3600,
        reset_at=None,
        synced_at=None,
        source=QuotaSource.REAL,
    )


class TestRefreshRestoresReauth:
    """Refresh success on a REAUTH_REQUIRED account restores ACTIVE."""

    @pytest.mark.asyncio
    async def test_refresh_success_restores_reauth_to_active(self, tmp_path):
        """Given: reauth-marked account + live quota fetch succeeds.
        When:  _refresh_one runs.
        Then:  account is ACTIVE, state_reason cleared, reauth keys removed.
        """
        repo = await _reauth_repo(tmp_path)
        svc = AccountRefreshService(repo)
        record = (await repo.get_accounts([REAUTH_TOKEN]))[0]

        with patch.object(
            svc,
            "_fetch_all_quotas",
            new_callable=AsyncMock,
            return_value={1: _fast_window()},
        ):
            result = await svc._refresh_one(record, apply_fallback=True)

        assert result.refreshed == 1
        assert result.failed == 0

        rec = (await repo.get_accounts([REAUTH_TOKEN]))[0]
        assert rec.status == AccountStatus.ACTIVE
        assert rec.state_reason is None
        assert "reauth_at" not in (rec.ext or {})
        assert "reauth_reason" not in (rec.ext or {})


class TestClearFailuresWipesReauth:
    """clear_failures removes reauth markers (status → ACTIVE, keys gone)."""

    @pytest.mark.asyncio
    async def test_clear_failures_removes_reauth_keys_local(self, tmp_path):
        """Given: REAUTH_REQUIRED record with reauth_at/reauth_reason in ext.
        When:  clear_failures patch applied (admin "clear failures").
        Then:  status ACTIVE, state_reason None, reauth keys removed.
        """
        repo = await _reauth_repo(tmp_path)

        await repo.patch_accounts(
            [AccountPatch(token=REAUTH_TOKEN, clear_failures=True)]
        )

        rec = (await repo.get_accounts([REAUTH_TOKEN]))[0]
        assert rec.status == AccountStatus.ACTIVE
        assert rec.state_reason is None
        assert "reauth_at" not in (rec.ext or {})
        assert "reauth_reason" not in (rec.ext or {})


class TestReauthRefreshCandidate:
    """REAUTH_REQUIRED accounts participate in scheduled refresh."""

    @pytest.mark.asyncio
    async def test_reauth_account_refresh_candidate(self, tmp_path):
        """Given: reauth-marked account in the repo.
        When:  refresh_scheduled() runs (fetch succeeds).
        Then:  the reauth account is selected for refresh and restored ACTIVE.
        """
        repo = await _reauth_repo(tmp_path)
        record = (await repo.get_accounts([REAUTH_TOKEN]))[0]
        assert is_manageable(record)  # REAUTH_REQUIRED is a refresh candidate

        svc = AccountRefreshService(repo)
        with patch.object(
            svc,
            "_fetch_all_quotas",
            new_callable=AsyncMock,
            return_value={1: _fast_window()},
        ) as mock_fetch:
            result = await svc.refresh_scheduled()

        assert mock_fetch.await_count == 1  # the reauth account was selected
        assert result.refreshed == 1

        rec = (await repo.get_accounts([REAUTH_TOKEN]))[0]
        assert rec.status == AccountStatus.ACTIVE
