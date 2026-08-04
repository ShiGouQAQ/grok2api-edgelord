"""Tests for mark_account_reauth_required (Go MarkReauthRequired port).

REAUTH_REQUIRED preserves the account — it leaves the selection pool but
stays recoverable, unlike EXPIRED which kills it.
"""

import pytest

from app.control.account.backends.local import LocalAccountRepository
from app.control.account.commands import AccountUpsert
from app.control.account.enums import AccountStatus
from app.control.account.invalid_credentials import (
    mark_account_invalid_credentials,
    mark_account_reauth_required,
)
from app.platform.errors import UpstreamError


async def _make_repo(tmp_path, *tokens: str) -> LocalAccountRepository:
    repo = LocalAccountRepository(tmp_path / "accounts.db")
    await repo.initialize()
    await repo.upsert_accounts([AccountUpsert(token=t) for t in tokens])
    return repo


async def _make_repo_with_provider(
    tmp_path, token: str, provider: str
) -> LocalAccountRepository:
    repo = LocalAccountRepository(tmp_path / "accounts.db")
    await repo.initialize()
    await repo.upsert_accounts(
        [AccountUpsert(token=token, provider=provider, pool="basic")]
    )
    return repo


@pytest.mark.asyncio
async def test_mark_reauth_sets_status_and_keys(tmp_path):
    """Marking reauth-required sets status/state_reason/ext keys and preserves the record."""
    repo = await _make_repo(tmp_path, "tok-1")

    ok = await mark_account_reauth_required(repo, "tok-1", "reason", source="test")

    assert ok is True
    record = next(iter(await repo.get_accounts(["tok-1"])), None)
    assert record is not None
    assert record.status == AccountStatus.REAUTH_REQUIRED
    assert record.state_reason == "reason"
    assert record.last_fail_reason == "reason"
    assert record.ext["reauth_at"] is not None
    assert record.ext["reauth_reason"] == "reason"
    assert record.is_deleted() is False


@pytest.mark.asyncio
async def test_mark_reauth_truncates_reason_512(tmp_path):
    """Reasons longer than 512 chars are truncated in both stored locations."""
    repo = await _make_repo(tmp_path, "tok-2")
    long_reason = "x" * 600

    await mark_account_reauth_required(repo, "tok-2", long_reason, source="test")

    record = next(iter(await repo.get_accounts(["tok-2"])), None)
    assert record is not None
    assert record.last_fail_reason is not None and len(record.last_fail_reason) == 512
    assert len(record.ext["reauth_reason"]) == 512


@pytest.mark.asyncio
async def test_mark_reauth_missing_account_returns_false(tmp_path):
    """Unknown tokens are not patched and return False."""
    repo = await _make_repo(tmp_path, "tok-3")

    ok = await mark_account_reauth_required(
        repo, "does-not-exist", "reason", source="test"
    )

    assert ok is False


@pytest.mark.asyncio
async def test_mark_reauth_does_not_expire(tmp_path):
    """Core semantic: REAUTH_REQUIRED is not EXPIRED — no expired_at key."""
    repo = await _make_repo(tmp_path, "tok-4")

    await mark_account_reauth_required(repo, "tok-4", "reason", source="test")

    record = next(iter(await repo.get_accounts(["tok-4"])), None)
    assert record is not None
    assert record.status == AccountStatus.REAUTH_REQUIRED
    assert record.status != AccountStatus.EXPIRED
    assert "expired_at" not in record.ext
    assert "expired_reason" not in record.ext


@pytest.mark.asyncio
async def test_mark_invalid_sso_account_routes_reauth(tmp_path):
    """Admin-path fix: a grok_web SSO account hitting an invalid-credentials
    error via mark_account_invalid_credentials must get REAUTH_REQUIRED
    (preserved), not EXPIRED — the SSO cookie may still work elsewhere."""
    repo = await _make_repo_with_provider(tmp_path, "sso-tok", "grok_web")
    exc = UpstreamError("denied", status=401, body="invalid-credentials")

    ok = await mark_account_invalid_credentials(repo, "sso-tok", exc, source="test")

    assert ok is True
    record = next(iter(await repo.get_accounts(["sso-tok"])), None)
    assert record is not None
    assert record.status == AccountStatus.REAUTH_REQUIRED
    assert "expired_at" not in record.ext
    assert "reauth_at" in record.ext


@pytest.mark.asyncio
async def test_mark_invalid_build_account_stays_expired(tmp_path):
    """Build-token death keeps EXPIRED — only build OAuth tokens hard-expire."""
    repo = await _make_repo_with_provider(tmp_path, "build-tok", "grok_build")
    exc = UpstreamError("denied", status=401, body="invalid-credentials")

    ok = await mark_account_invalid_credentials(repo, "build-tok", exc, source="test")

    assert ok is True
    record = next(iter(await repo.get_accounts(["build-tok"])), None)
    assert record is not None
    assert record.status == AccountStatus.EXPIRED
    assert "expired_at" in record.ext
    assert "reauth_at" not in record.ext


@pytest.mark.asyncio
async def test_mark_invalid_console_account_routes_reauth(tmp_path):
    """Console SSO accounts (provider grok_console) also route to REAUTH."""
    repo = await _make_repo_with_provider(tmp_path, "con-tok", "grok_console")
    exc = UpstreamError("denied", status=403, body="session-expired")

    ok = await mark_account_invalid_credentials(repo, "con-tok", exc, source="test")

    assert ok is True
    record = next(iter(await repo.get_accounts(["con-tok"])), None)
    assert record is not None
    assert record.status == AccountStatus.REAUTH_REQUIRED
