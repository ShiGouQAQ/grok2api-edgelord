"""REAUTH_REQUIRED stuck-account recovery (M7).

A genuinely-dead SSO account stays REAUTH_REQUIRED forever: scheduled refresh
keeps retrying it (REAUTH_REQUIRED is manageable) and each credential-rejected
failure re-marks REAUTH.  A consecutive-failure counter (ext.reauth_fail_count)
plus a leader sweep task bound the retry: once the counter reaches the
threshold the account is marked EXPIRED and leaves the refresh pool.
"""

import pytest

from app.control.account.backends.local import LocalAccountRepository
from app.control.account.commands import AccountPatch, AccountUpsert
from app.control.account.enums import AccountStatus
from app.control.account.invalid_credentials import mark_account_reauth_required
from app.control.account.recovery import (
    REAUTH_FAIL_COUNT_KEY,
    REAUTH_STUCK_REASON,
    bump_reauth_fail_count,
    recover_stuck_reauth_accounts,
)
from app.control.account.refresh import AccountRefreshService
from app.platform.errors import UpstreamError

TOKEN = "stuck-reauth-tok"


async def _make_repo(tmp_path, *upserts: AccountUpsert) -> LocalAccountRepository:
    repo = LocalAccountRepository(tmp_path / "accounts.db")
    await repo.initialize()
    if upserts:
        await repo.upsert_accounts(list(upserts))
    return repo


async def _reauth_repo(tmp_path, *, fail_count: int) -> LocalAccountRepository:
    repo = await _make_repo(
        tmp_path,
        AccountUpsert(token=TOKEN, pool="basic", provider="grok_web"),
    )
    await mark_account_reauth_required(repo, TOKEN, "reauth: 401", source="test")
    if fail_count > 0:
        await repo.patch_accounts(
            [
                AccountPatch(
                    token=TOKEN,
                    ext_merge={REAUTH_FAIL_COUNT_KEY: fail_count},
                )
            ]
        )
    return repo


class TestBumpReauthFailCount:
    @pytest.mark.asyncio
    async def test_bump_increments_while_already_reauth(self, tmp_path):
        repo = await _reauth_repo(tmp_path, fail_count=1)
        record = (await repo.get_accounts([TOKEN]))[0]
        assert await bump_reauth_fail_count(repo, record) == 2
        rec = (await repo.get_accounts([TOKEN]))[0]
        assert rec.ext.get(REAUTH_FAIL_COUNT_KEY) == 2

    @pytest.mark.asyncio
    async def test_bump_resets_to_one_on_fresh_transition(self, tmp_path):
        repo = await _make_repo(
            tmp_path,
            AccountUpsert(token=TOKEN, pool="basic", provider="grok_web"),
        )
        record = (await repo.get_accounts([TOKEN]))[0]
        assert record.status == AccountStatus.ACTIVE
        assert await bump_reauth_fail_count(repo, record) == 1

    @pytest.mark.asyncio
    async def test_expire_invalid_credentials_counts_consecutive_failures(
        self, tmp_path
    ):
        repo = await _reauth_repo(tmp_path, fail_count=0)
        svc = AccountRefreshService(repo)
        exc = UpstreamError("sso credential rejected", credential_rejected=True)
        record = (await repo.get_accounts([TOKEN]))[0]

        assert await svc._expire_invalid_credentials(record, exc) is True
        rec = (await repo.get_accounts([TOKEN]))[0]
        assert rec.status == AccountStatus.REAUTH_REQUIRED
        assert rec.ext.get(REAUTH_FAIL_COUNT_KEY) == 1

        record = (await repo.get_accounts([TOKEN]))[0]
        assert await svc._expire_invalid_credentials(record, exc) is True
        rec = (await repo.get_accounts([TOKEN]))[0]
        assert rec.ext.get(REAUTH_FAIL_COUNT_KEY) == 2


class TestRecoverStuckReauthAccounts:
    @pytest.mark.asyncio
    async def test_at_threshold_marks_expired(self, tmp_path):
        repo = await _reauth_repo(tmp_path, fail_count=3)
        count = await recover_stuck_reauth_accounts(repo, threshold=3)
        assert count == 1
        rec = (await repo.get_accounts([TOKEN]))[0]
        assert rec.status == AccountStatus.EXPIRED
        assert rec.state_reason == REAUTH_STUCK_REASON
        assert rec.ext.get("expired_reason") == REAUTH_STUCK_REASON

    @pytest.mark.asyncio
    async def test_above_threshold_marks_expired(self, tmp_path):
        repo = await _reauth_repo(tmp_path, fail_count=5)
        assert await recover_stuck_reauth_accounts(repo, threshold=3) == 1
        rec = (await repo.get_accounts([TOKEN]))[0]
        assert rec.status == AccountStatus.EXPIRED

    @pytest.mark.asyncio
    async def test_below_threshold_stays_reauth(self, tmp_path):
        repo = await _reauth_repo(tmp_path, fail_count=2)
        assert await recover_stuck_reauth_accounts(repo, threshold=3) == 0
        rec = (await repo.get_accounts([TOKEN]))[0]
        assert rec.status == AccountStatus.REAUTH_REQUIRED

    @pytest.mark.asyncio
    async def test_no_counter_stays_reauth(self, tmp_path):
        repo = await _reauth_repo(tmp_path, fail_count=0)
        assert await recover_stuck_reauth_accounts(repo, threshold=3) == 0
        rec = (await repo.get_accounts([TOKEN]))[0]
        assert rec.status == AccountStatus.REAUTH_REQUIRED

    @pytest.mark.asyncio
    async def test_active_account_never_expired(self, tmp_path):
        repo = await _make_repo(
            tmp_path,
            AccountUpsert(token=TOKEN, pool="basic", provider="grok_web"),
        )
        await repo.patch_accounts(
            [
                AccountPatch(
                    token=TOKEN,
                    ext_merge={REAUTH_FAIL_COUNT_KEY: 99},
                )
            ]
        )
        assert await recover_stuck_reauth_accounts(repo, threshold=3) == 0
        rec = (await repo.get_accounts([TOKEN]))[0]
        assert rec.status == AccountStatus.ACTIVE
