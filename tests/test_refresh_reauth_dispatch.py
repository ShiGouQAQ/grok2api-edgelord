"""Tests for refresh-path invalid-credential dispatch.

Semantics: a credential_rejected flag on an SSO-class account (grok_web /
grok_console) means the *converted* credential was rejected at pre-check —
the SSO cookie itself may still work on Web/Console — so the account must be
preserved as REAUTH_REQUIRED, not killed as EXPIRED.  Body-marker-confirmed
deaths and Build OAuth 401s stay EXPIRED.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.control.account.backends.local import LocalAccountRepository
from app.control.account.commands import AccountUpsert
from app.control.account.enums import AccountStatus
from app.control.account.refresh import AccountRefreshService
from app.platform.errors import UpstreamError


async def _make_repo(tmp_path, *upserts: AccountUpsert) -> LocalAccountRepository:
    """Create a fresh LocalAccountRepository with the given accounts."""
    repo = LocalAccountRepository(tmp_path / "accounts.db")
    await repo.initialize()
    if upserts:
        await repo.upsert_accounts(list(upserts))
    return repo


def _console_account(token: str = "console-tok") -> AccountUpsert:
    return AccountUpsert(token=token, pool="basic", provider="grok_console")


def _build_account(token: str = "build-tok") -> AccountUpsert:
    return AccountUpsert(
        token=token,
        pool="build",
        provider="grok_build",
        ext={"build_access_token": "at-build", "build_refresh_token": "rt-build"},
    )


@pytest.mark.asyncio
async def test_console_credential_rejected_routes_reauth(tmp_path):
    """Console usage-refresh 401 with credential_rejected → REAUTH_REQUIRED,
    never EXPIRED: the SSO cookie may still be valid for Web/Console."""
    repo = await _make_repo(tmp_path, _console_account())
    svc = AccountRefreshService(repo)
    record = (await repo.get_accounts(["console-tok"]))[0]
    exc = UpstreamError(
        "Console access denied: HTTP 401", status=401, credential_rejected=True
    )

    marked = await svc._expire_invalid_credentials(record, exc)

    assert marked is True
    rec = (await repo.get_accounts(["console-tok"]))[0]
    assert rec.status == AccountStatus.REAUTH_REQUIRED
    assert rec.status != AccountStatus.EXPIRED
    assert rec.state_reason == str(exc)
    assert (rec.ext or {}).get("reauth_reason") == str(exc)
    assert "expired_at" not in (rec.ext or {})
    assert "expired_reason" not in (rec.ext or {})


@pytest.mark.asyncio
async def test_body_marker_401_routes_reauth(tmp_path):
    """Body-marker-confirmed invalid credentials on an SSO-class (console)
    account route to REAUTH_REQUIRED, not EXPIRED — the SSO cookie may still
    work elsewhere. Unifies 400/401/403 (the 400 path lacks the structured
    credential_rejected flag, so the body-marker check must cover it)."""
    repo = await _make_repo(tmp_path, _console_account())
    svc = AccountRefreshService(repo)
    record = (await repo.get_accounts(["console-tok"]))[0]
    exc = UpstreamError(
        "Console access denied: HTTP 401", status=401, body="invalid-credentials"
    )

    marked = await svc._expire_invalid_credentials(record, exc)

    assert marked is True
    rec = (await repo.get_accounts(["console-tok"]))[0]
    assert rec.status == AccountStatus.REAUTH_REQUIRED
    assert rec.state_reason == "Console access denied: HTTP 401"


@pytest.mark.asyncio
async def test_body_marker_400_routes_reauth(tmp_path):
    """HTTP 400 with an invalid-credentials body (no structured flag —
    _classify_upstream_status has no 400 branch) must still route the SSO
    account to REAUTH_REQUIRED, matching the 401/403 path."""
    repo = await _make_repo(tmp_path, _console_account())
    svc = AccountRefreshService(repo)
    record = (await repo.get_accounts(["console-tok"]))[0]
    exc = UpstreamError(
        "Console access denied: HTTP 400", status=400, body="session-expired"
    )

    marked = await svc._expire_invalid_credentials(record, exc)

    assert marked is True
    rec = (await repo.get_accounts(["console-tok"]))[0]
    assert rec.status == AccountStatus.REAUTH_REQUIRED
    assert rec.state_reason == "Console access denied: HTTP 400"


@pytest.mark.asyncio
async def test_build_billing_401_stays_expired(tmp_path):
    """Build OAuth billing 401 (credential_rejected) keeps EXPIRED — the
    force-EXPIRED path must not be diverted by the reauth dispatch."""
    repo = await _make_repo(tmp_path, _build_account())
    svc = AccountRefreshService(repo)
    record = (await repo.get_accounts(["build-tok"]))[0]

    with patch(
        "app.dataplane.reverse.protocol.xai_billing.fetch_build_billing",
        new_callable=AsyncMock,
        side_effect=UpstreamError(
            "Build billing access denied: HTTP 401",
            status=401,
            credential_rejected=True,
        ),
    ):
        result = await svc._refresh_one(record, apply_fallback=True)

    assert result.checked == 1
    assert result.expired == 1
    assert result.failed == 0
    rec = (await repo.get_accounts(["build-tok"]))[0]
    assert rec.status == AccountStatus.EXPIRED
    assert rec.state_reason == "invalid_credentials"
