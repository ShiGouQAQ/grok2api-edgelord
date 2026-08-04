"""Tests for build-account quota refresh routing (billing API, not rate-limits).

Build (pool="build" / provider="grok_build") accounts must probe
api.x.ai/billing/usage instead of grok.com/rest/rate-limits — sending an
OAuth access token as an sso cookie always 401s and wrongly EXPIRES the
account.  These tests lock that routing and the failure semantics.
"""

import tempfile
from dataclasses import asdict
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.control.account.backends.local import LocalAccountRepository
from app.control.account.commands import AccountUpsert
from app.control.account.enums import AccountStatus
from app.control.account.refresh import AccountRefreshService
from app.control.account.scheduler import _POOL_CONFIG
from app.dataplane.reverse.protocol.xai_billing import BuildBilling
from app.platform.errors import UpstreamError


async def _make_repo(*upserts: AccountUpsert) -> LocalAccountRepository:
    """Create a fresh LocalAccountRepository with the given accounts."""
    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "accounts.db"
    repo = LocalAccountRepository(db_path)
    await repo.initialize()
    if upserts:
        await repo.upsert_accounts(list(upserts))
    return repo


def _build_account() -> AccountUpsert:
    """A standard build account with an OAuth access token in ext."""
    return AccountUpsert(
        token="build-tok",
        pool="build",
        provider="grok_build",
        ext={
            "build_access_token": "at-build",
            "build_refresh_token": "rt-build",
        },
    )


def _billing() -> BuildBilling:
    return BuildBilling(
        plan_code="free",
        plan_name="Free",
        monthly_limit=100,
        used=10,
        on_demand_cap=0,
        on_demand_used=0,
        prepaid_balance=0,
    )


class TestBuildBillingRouting:
    """_refresh_one routes build accounts to the billing probe."""

    @pytest.mark.asyncio
    async def test_build_account_routes_to_billing(self):
        """Build record + billing success → ext.build_billing patched,
        rate-limits fetch never called, refreshed=1."""
        repo = await _make_repo(_build_account())
        svc = AccountRefreshService(repo)
        record = (await repo.get_accounts(["build-tok"]))[0]
        billing = _billing()
        with (
            # Spy on the grok.com rate-limits path — must NOT be touched.
            patch.object(
                svc, "_fetch_all_quotas", new_callable=AsyncMock
            ) as mock_quotas,
            patch(
                "app.dataplane.reverse.protocol.xai_billing.fetch_build_billing",
                new_callable=AsyncMock,
                return_value=billing,
            ) as mock_billing,
        ):
            result = await svc._refresh_one(record, apply_fallback=True)

        assert result.checked == 1
        assert result.refreshed == 1
        assert result.failed == 0
        mock_billing.assert_awaited_once_with("at-build")
        mock_quotas.assert_not_called()

        rec = (await repo.get_accounts(["build-tok"]))[0]
        assert rec.ext["build_billing"] == asdict(billing)

    @pytest.mark.asyncio
    async def test_build_billing_401_expires_account(self):
        """Billing 401 (credential_rejected) → account EXPIRED, expired=1."""
        repo = await _make_repo(_build_account())
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

    @pytest.mark.asyncio
    async def test_build_billing_network_error_fails(self):
        """Billing transport error → failed=1, account NOT expired."""
        repo = await _make_repo(_build_account())
        svc = AccountRefreshService(repo)
        record = (await repo.get_accounts(["build-tok"]))[0]

        with patch(
            "app.dataplane.reverse.protocol.xai_billing.fetch_build_billing",
            new_callable=AsyncMock,
            side_effect=RuntimeError("network down"),
        ):
            result = await svc._refresh_one(record, apply_fallback=True)

        assert result.checked == 1
        assert result.failed == 1
        assert result.expired == 0

        rec = (await repo.get_accounts(["build-tok"]))[0]
        assert rec.status == AccountStatus.ACTIVE
        assert "build_billing" not in (rec.ext or {})

    @pytest.mark.asyncio
    async def test_refresh_scheduled_none_routes_build_to_billing(self):
        """Mixed build + web records: build → billing, web → rate-limits."""
        repo = await _make_repo(
            _build_account(),
            AccountUpsert(token="web-tok", pool="basic", provider="grok_web"),
        )
        svc = AccountRefreshService(repo)

        billing = _billing()
        with (
            patch(
                "app.dataplane.reverse.protocol.xai_billing.fetch_build_billing",
                new_callable=AsyncMock,
                return_value=billing,
            ) as mock_billing,
            patch(
                "app.dataplane.reverse.protocol.xai_usage.fetch_all_quotas",
                new_callable=AsyncMock,
                return_value={},
            ) as mock_usage,
        ):
            result = await svc.refresh_scheduled()

        assert result.checked == 2
        assert result.refreshed == 1  # only the build account
        mock_billing.assert_awaited_once_with("at-build")
        mock_usage.assert_awaited_once_with("web-tok", (1, 5, 6))

        recs = {r.token: r for r in await repo.get_accounts(["build-tok", "web-tok"])}
        assert "build_billing" in (recs["build-tok"].ext or {})
        assert "build_billing" not in (recs["web-tok"].ext or {})


class TestBuildBillingQuotaClassification:
    """b4c7baab: quota signals in refresh classification.

    Go ClassifyCredentialRejection decoupled quota exhaustion from credential
    rejection (QuotaExhausted/FreeQuotaExhausted/ModelQuotaExhausted flags).
    The billing probe must therefore NOT expire an account on quota bodies —
    per-account failed outcome, account stays in the pool.
    """

    @pytest.mark.asyncio
    async def test_billing_403_credit_marker_not_expired(self):
        """403 with 'usage limit reached' body → failed=1, account stays ACTIVE.

        Regression: fetch_build_billing sets credential_rejected=True on any
        403; the credit-exhaustion marker must win over that (Go b4c7baab)."""
        repo = await _make_repo(_build_account())
        svc = AccountRefreshService(repo)
        record = (await repo.get_accounts(["build-tok"]))[0]

        with patch(
            "app.dataplane.reverse.protocol.xai_billing.fetch_build_billing",
            new_callable=AsyncMock,
            side_effect=UpstreamError(
                "Build billing access denied: HTTP 403",
                status=403,
                credential_rejected=True,
                body='{"error": "usage limit reached"}',
            ),
        ):
            result = await svc._refresh_one(record, apply_fallback=True)

        assert result.checked == 1
        assert result.failed == 1
        assert result.expired == 0

        rec = (await repo.get_accounts(["build-tok"]))[0]
        assert rec.status == AccountStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_billing_402_quota_exhausted_not_expired(self):
        """402 with quota_exhausted flag → failed, not expired."""
        repo = await _make_repo(_build_account())
        svc = AccountRefreshService(repo)
        record = (await repo.get_accounts(["build-tok"]))[0]

        with patch(
            "app.dataplane.reverse.protocol.xai_billing.fetch_build_billing",
            new_callable=AsyncMock,
            side_effect=UpstreamError(
                "Build billing payment required",
                status=402,
                quota_exhausted=True,
                body='{"error": "run out of credits"}',
            ),
        ):
            result = await svc._refresh_one(record, apply_fallback=True)

        assert result.failed == 1
        assert result.expired == 0
        rec = (await repo.get_accounts(["build-tok"]))[0]
        assert rec.status == AccountStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_billing_429_free_usage_exhausted_not_expired(self):
        """429 free_quota_exhausted flag → failed, not expired."""
        repo = await _make_repo(_build_account())
        svc = AccountRefreshService(repo)
        record = (await repo.get_accounts(["build-tok"]))[0]

        with patch(
            "app.dataplane.reverse.protocol.xai_billing.fetch_build_billing",
            new_callable=AsyncMock,
            side_effect=UpstreamError(
                "Build billing rate limited",
                status=429,
                free_quota_exhausted=True,
                body='{"code": "subscription:free-usage-exhausted"}',
            ),
        ):
            result = await svc._refresh_one(record, apply_fallback=True)

        assert result.failed == 1
        assert result.expired == 0
        rec = (await repo.get_accounts(["build-tok"]))[0]
        assert rec.status == AccountStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_billing_401_credential_still_expires(self):
        """401 credential rejection (no quota markers) still EXPIRES — the
        quota bridge must not swallow genuine credential deaths."""
        repo = await _make_repo(_build_account())
        svc = AccountRefreshService(repo)
        record = (await repo.get_accounts(["build-tok"]))[0]

        with patch(
            "app.dataplane.reverse.protocol.xai_billing.fetch_build_billing",
            new_callable=AsyncMock,
            side_effect=UpstreamError(
                "Build billing access denied: HTTP 401",
                status=401,
                credential_rejected=True,
                body="invalid-credentials",
            ),
        ):
            result = await svc._refresh_one(record, apply_fallback=True)

        assert result.expired == 1
        rec = (await repo.get_accounts(["build-tok"]))[0]
        assert rec.status == AccountStatus.EXPIRED


class TestSchedulerBuildPool:
    """Scheduler includes the build pool with a sane default interval."""

    def test_scheduler_pool_config_includes_build(self):
        assert "build" in _POOL_CONFIG
        key, default = _POOL_CONFIG["build"]
        assert key == "account.refresh.build_interval_sec"
        assert default == 21_600
