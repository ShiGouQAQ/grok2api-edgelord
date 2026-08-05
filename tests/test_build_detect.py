"""Tests for admin Build-account detection (port of Go bcc6435f + b4c7baab).

Detection probes each Build account with a fixed non-streaming POST
/responses (model grok-4.5, input "hello,test") and classifies:
  ok      — 2xx response
  invalid — OAuth credential rejected (401 after one manual refresh retry,
            or permanent refresh failure) → REAUTH_REQUIRED
  failed  — network error / quota exhaustion / model denial / non-2xx
            (no account-state change; softNetworkCooldown: network errors
            never bump reauth/expired counters)
"""

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.control.account.backends.local import LocalAccountRepository
from app.control.account.commands import AccountUpsert
from app.control.account.enums import AccountStatus
from app.control.account.sso_build import (
    BuildCredentialSeed,
    _acquire_mint_lease,
)
from app.platform.errors import UpstreamError
from app.platform.runtime.clock import now_ms


async def _make_repo(*upserts: AccountUpsert) -> LocalAccountRepository:
    """Create a fresh LocalAccountRepository with the given accounts."""
    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "accounts.db"
    repo = LocalAccountRepository(db_path)
    await repo.initialize()
    if upserts:
        await repo.upsert_accounts(list(upserts))
    return repo


def _build_account(ext: dict[str, Any] | None = None) -> AccountUpsert:
    """A standard build account with an OAuth access token in ext."""
    base = {
        "build_access_token": "at-build",
        "build_refresh_token": "rt-build",
        "build_expires_at": now_ms() + 3_600_000,  # access token alive
    }
    if ext:
        base.update(ext)
    return AccountUpsert(
        token="build-tok",
        pool="build",
        provider="grok_build",
        ext=base,
    )


async def _detect(
    repo: LocalAccountRepository, token: str = "build-tok"
) -> dict[str, Any]:
    from app.control.account.build_detect import detect_build_account

    return await detect_build_account(repo, token)


class TestDetectOutcomeClassification:
    """bcc6435f detection outcome classification (ok/invalid/failed)."""

    @pytest.mark.asyncio
    async def test_detect_success_ok(self):
        """2xx probe → outcome ok, account state untouched."""
        repo = await _make_repo(_build_account())

        with patch(
            "app.control.account.build_detect._probe_build",
            AsyncMock(return_value=(200, '{"id":"resp-1"}')),
        ):
            result = await _detect(repo)

        assert result["outcome"] == "ok"
        assert result["httpStatus"] == 200
        rec = (await repo.get_accounts(["build-tok"]))[0]
        assert rec.status == AccountStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_detect_401_refresh_once_then_ok(self):
        """401 → refresh credentials once → retry → 200 → ok."""
        repo = await _make_repo(_build_account())

        with (
            patch(
                "app.control.account.build_detect._probe_build",
                AsyncMock(
                    side_effect=[
                        (401, '{"error":"invalid token"}'),
                        (200, '{"id":"resp-2"}'),
                    ]
                ),
            ),
            patch(
                "app.control.account.build_detect.refresh_build_token_manual",
                new_callable=AsyncMock,
                return_value=BuildCredentialSeed(
                    access_token="at-new", refresh_token="rt-new", expires_in=3600
                ),
            ) as mock_manual,
        ):
            result = await _detect(repo)

        assert result["outcome"] == "ok"
        mock_manual.assert_awaited_once()
        rec = (await repo.get_accounts(["build-tok"]))[0]
        assert rec.status == AccountStatus.ACTIVE
        # refreshed credential persisted (ext carries the new access token)
        assert (rec.ext or {})["build_access_token"] == "at-new"

    @pytest.mark.asyncio
    async def test_detect_second_401_marks_invalid_reauth(self):
        """401 → refresh → second 401 → invalid + REAUTH_REQUIRED."""
        repo = await _make_repo(_build_account())

        with (
            patch(
                "app.control.account.build_detect._probe_build",
                AsyncMock(
                    side_effect=[
                        (401, '{"error":"invalid token"}'),
                        (401, '{"error":"invalid token"}'),
                    ]
                ),
            ),
            patch(
                "app.control.account.build_detect.refresh_build_token_manual",
                new_callable=AsyncMock,
                return_value=BuildCredentialSeed(
                    access_token="at-new", refresh_token="rt-new", expires_in=3600
                ),
            ),
        ):
            result = await _detect(repo)

        assert result["outcome"] == "invalid"
        rec = (await repo.get_accounts(["build-tok"]))[0]
        assert rec.status == AccountStatus.REAUTH_REQUIRED
        assert "reauth_reason" in (rec.ext or {})

    @pytest.mark.asyncio
    async def test_detect_network_error_failed_no_counters(self):
        """Network error (status==0) → failed; NO reauth/expired counter bump.

        softNetworkCooldown semantics: transport failures never accumulate
        fail count against the account (Go selector_session softNetworkCooldown
        5s — the 5s isolation itself is routing-side, Wave-2 F)."""
        repo = await _make_repo(_build_account())

        with patch(
            "app.control.account.build_detect._probe_build",
            AsyncMock(
                side_effect=UpstreamError("Build detection transport failed", status=0)
            ),
        ):
            result = await _detect(repo)

        assert result["outcome"] == "failed"
        assert result["httpStatus"] == 0
        rec = (await repo.get_accounts(["build-tok"]))[0]
        assert rec.status == AccountStatus.ACTIVE
        assert "reauth_at" not in (rec.ext or {})
        assert "expired_at" not in (rec.ext or {})

    @pytest.mark.asyncio
    async def test_detect_403_blocked_user_reauth_without_config_gate(self):
        """403 blocked-user reauths in detect WITHOUT the config gate
        (Go ClassifyCredentialRejection: 403 credential keyword → Rejected
        → unconditional markBuildDetectReauth)."""
        repo = await _make_repo(_build_account())

        with patch(
            "app.control.account.build_detect._probe_build",
            AsyncMock(return_value=(403, '{"error":"blocked-user"}')),
        ):
            result = await _detect(repo)

        assert result["outcome"] == "invalid"
        assert result["httpStatus"] == 403
        rec = (await repo.get_accounts(["build-tok"]))[0]
        assert rec.status == AccountStatus.REAUTH_REQUIRED

    @pytest.mark.asyncio
    async def test_detect_403_invalid_token_reauth_without_config_gate(self):
        """403 'invalid token' → REAUTH_REQUIRED with the DEFAULT config
        (mark_build_chat_denied_as_reauth=false must not gate the detect
        path — Go finishBuildDetectResponse has no config gate)."""
        repo = await _make_repo(_build_account())

        with patch(
            "app.control.account.build_detect._probe_build",
            AsyncMock(return_value=(403, '{"error":"invalid token"}')),
        ):
            result = await _detect(repo)

        assert result["outcome"] == "invalid"
        assert result["httpStatus"] == 403
        rec = (await repo.get_accounts(["build-tok"]))[0]
        assert rec.status == AccountStatus.REAUTH_REQUIRED
        assert "reauth_reason" in (rec.ext or {})

    @pytest.mark.asyncio
    async def test_detect_403_permanent_denial_5min_cooldown(self):
        """403 'chat endpoint denied' (permanent account denial) → failed
        outcome (shape unchanged) + 5 min model-denied cooldown persisted
        (Go markBuildDetectModelDenied → ModelQuotaBlock
        'model_access_denied' CooldownUntil=now+5min)."""
        repo = await _make_repo(_build_account())

        with patch(
            "app.control.account.build_detect._probe_build",
            AsyncMock(
                return_value=(403, '{"error":"Access to the chat endpoint is denied"}')
            ),
        ):
            result = await _detect(repo)

        assert result["outcome"] == "failed"
        assert result["httpStatus"] == 403
        rec = (await repo.get_accounts(["build-tok"]))[0]
        assert rec.status == AccountStatus.COOLING
        ext = rec.ext or {}
        cooldown_until = int(ext["cooldown_until"])
        # ≈ now + 5 min (Go buildDetectModelDeniedCooldown)
        assert 4 * 60_000 <= cooldown_until - now_ms() <= 6 * 60_000
        assert ext.get("cooldown_reason") == "model_access_denied"

    @pytest.mark.asyncio
    async def test_detect_quota_429_schedules_24h_recovery(self):
        """429 free-usage-exhausted → failed + quota_build window gains
        reset_at ≈ now+24h with predicted=True (Go markBuildDetectQuotaExhausted
        → SaveQuotaRecovery NextProbeAt=now+buildDetectQuotaRecoveryPause)."""
        repo = await _make_repo(_build_account())
        before = now_ms()

        with patch(
            "app.control.account.build_detect._probe_build",
            AsyncMock(
                return_value=(
                    429,
                    '{"code":"subscription:free-usage-exhausted","error":"You\'ve used all the included free usage for model grok-4.5-build-free for now. Usage resets over a rolling 24-hour window — tokens (actual/limit): 537365/500000."}',
                )
            ),
        ):
            result = await _detect(repo)

        assert result["outcome"] == "failed"
        rec = (await repo.get_accounts(["build-tok"]))[0]
        assert rec.status == AccountStatus.ACTIVE  # quota is not a credential death
        win = rec.quota_set().quota_build
        assert win is not None
        assert win.remaining == 0
        assert win.predicted is True
        reset_at = win.reset_at
        assert reset_at is not None
        assert before + 24 * 3_600_000 <= reset_at <= now_ms() + 24 * 3_600_000

    @pytest.mark.asyncio
    async def test_detect_403_quota_text_not_reauth(self):
        """403 with quota text → NOT reauth: quota branch (failed + 24 h
        recovery window), account stays ACTIVE (Go Rejected requires
        !QuotaExhausted)."""
        repo = await _make_repo(_build_account())

        with patch(
            "app.control.account.build_detect._probe_build",
            AsyncMock(
                return_value=(
                    403,
                    '{"error":"You\'ve used all the included free usage for model grok-4.5-build-free for now. Usage resets over a rolling 24-hour window."}',
                )
            ),
        ):
            result = await _detect(repo)

        assert result["outcome"] == "failed"
        rec = (await repo.get_accounts(["build-tok"]))[0]
        assert rec.status == AccountStatus.ACTIVE
        assert "reauth_reason" not in (rec.ext or {})
        win = rec.quota_set().quota_build
        assert win is not None
        assert win.remaining == 0
        assert win.predicted is True

    @pytest.mark.asyncio
    async def test_detect_402_spending_limit_schedules_24h_recovery(self):
        """402 personal-team-blocked:spending-limit (Go SpendingLimitBlocked)
        → failed + 24 h recovery window."""
        repo = await _make_repo(_build_account())

        with patch(
            "app.control.account.build_detect._probe_build",
            AsyncMock(
                return_value=(402, '{"code":"personal-team-blocked:spending-limit"}')
            ),
        ):
            result = await _detect(repo)

        assert result["outcome"] == "failed"
        rec = (await repo.get_accounts(["build-tok"]))[0]
        assert rec.status == AccountStatus.ACTIVE
        win = rec.quota_set().quota_build
        assert win is not None
        assert win.remaining == 0
        assert win.predicted is True

    @pytest.mark.asyncio
    async def test_detect_non_build_account_failed(self):
        """Web account → failed '仅 Grok Build 账号支持可用性检测'."""
        repo = await _make_repo(
            AccountUpsert(token="web-tok", pool="basic", provider="grok_web")
        )

        result = await _detect(repo, token="web-tok")

        assert result["outcome"] == "failed"
        assert "Grok Build" in result["reason"]


class TestDetectManualRetry:
    """ef10c4cb: manual (admin) retry bypasses the permanent short-circuit
    exactly once; after that one retry the permanent status returns."""

    @pytest.mark.asyncio
    async def test_manual_retry_bypasses_permanent_short_circuit_once(self):
        """Permanent-marked account + alive access token: detection 401 path
        still issues exactly one manual refresh (bypass); failure re-applies
        the permanent marker and marks invalid."""
        repo = await _make_repo(
            _build_account(
                ext={
                    "build_refresh_permanent": True,
                    "build_refresh_error": "refresh_token_invalid",
                }
            )
        )

        with (
            patch(
                "app.control.account.build_detect._probe_build",
                AsyncMock(return_value=(401, '{"error":"invalid token"}')),
            ),
            patch(
                "app.control.account.build_detect.refresh_build_token_manual",
                new_callable=AsyncMock,
                return_value=None,  # permanent failure again
            ) as mock_manual,
        ):
            result = await _detect(repo)

        assert result["outcome"] == "invalid"
        mock_manual.assert_awaited_once()  # exactly one bypass attempt
        rec = (await repo.get_accounts(["build-tok"]))[0]
        assert rec.status == AccountStatus.REAUTH_REQUIRED
        # permanent status returns after the single retry
        assert (rec.ext or {})["build_refresh_permanent"] is True

    @pytest.mark.asyncio
    async def test_manual_retry_recovers_permanent_account(self):
        """Permanent-marked account: one manual retry succeeds → ok and the
        permanent marker is cleared (Go: success clears RefreshPermanent)."""
        repo = await _make_repo(
            _build_account(
                ext={
                    "build_refresh_permanent": True,
                    "build_refresh_error": "refresh_token_invalid",
                }
            )
        )

        with (
            patch(
                "app.control.account.build_detect._probe_build",
                AsyncMock(
                    side_effect=[
                        (401, '{"error":"invalid token"}'),
                        (200, '{"id":"resp-1"}'),
                    ]
                ),
            ),
            patch(
                "app.control.account.build_detect.refresh_build_token_manual",
                new_callable=AsyncMock,
                return_value=BuildCredentialSeed(
                    access_token="at-new", refresh_token="rt-new", expires_in=3600
                ),
            ),
        ):
            result = await _detect(repo)

        assert result["outcome"] == "ok"
        rec = (await repo.get_accounts(["build-tok"]))[0]
        assert rec.status == AccountStatus.ACTIVE
        assert (rec.ext or {}).get("build_refresh_permanent") in (None, False)

    @pytest.mark.asyncio
    async def test_manual_retry_in_flight_guard_singleflight(self):
        """:manual-retry guard: a second manual retry while one is in flight
        raises without issuing another OAuth request (Go singleflight naming)."""
        from app.control.account import build_refresh
        from app.control.account.build_refresh import refresh_build_token_manual

        key = "acc-guard"
        build_refresh._IN_FLIGHT_MANUAL_RETRIES.add(f"{key}:manual-retry")
        try:
            with patch(
                "app.control.account.build_refresh.refresh_build_token",
                new_callable=AsyncMock,
            ) as mock_refresh:
                with pytest.raises(RuntimeError):
                    await refresh_build_token_manual(key, "rt")
            mock_refresh.assert_not_awaited()
        finally:
            build_refresh._IN_FLIGHT_MANUAL_RETRIES.discard(f"{key}:manual-retry")

    @pytest.mark.asyncio
    async def test_detect_401_no_refresh_token_marks_invalid(self):
        """401 without a refresh token → invalid (cannot refresh)."""
        repo = await _make_repo(_build_account(ext={"build_refresh_token": ""}))

        with patch(
            "app.control.account.build_detect._probe_build",
            AsyncMock(return_value=(401, '{"error":"invalid token"}')),
        ):
            result = await _detect(repo)

        assert result["outcome"] == "invalid"
        rec = (await repo.get_accounts(["build-tok"]))[0]
        assert rec.status == AccountStatus.REAUTH_REQUIRED


class TestDetectEndpointValidation:
    """bcc6435f config keys: account.build_detect.max_attempts rejects 0."""

    @pytest.mark.asyncio
    async def test_endpoint_rejects_zero_max_attempts(self):
        from fastapi import HTTPException

        from app.products.web.admin.batch import (
            BatchRequest,
            batch_build_detect,
        )
        from app.platform.errors import ValidationError

        repo = AsyncMock()
        with patch(
            "app.products.web.admin.batch.get_config",
            return_value=0,
        ):
            with pytest.raises((ValidationError, HTTPException)):
                await batch_build_detect(
                    BatchRequest(tokens=["build-tok"]),
                    async_mode=False,
                    all_build=False,
                    concurrency=None,
                    repo=repo,
                )


class TestSsoMintLeaseScope:
    """Wave-1-D handoff: Build mint acquires with ProxyScope.BUILD so
    proxy_pool rotates a fresh tunnel per mint request."""

    @pytest.mark.asyncio
    async def test_acquire_mint_lease_uses_build_scope(self):
        from app.control.proxy.models import ProxyScope

        proxy = AsyncMock()
        lease = AsyncMock()
        lease.cf_cookies = "cf_clearance=abc"
        proxy.acquire.return_value = lease

        with patch(
            "app.dataplane.proxy.get_proxy_runtime",
            AsyncMock(return_value=proxy),
        ):
            got = await _acquire_mint_lease()

        assert got is lease
        proxy.acquire.assert_awaited_once()
        kwargs = proxy.acquire.await_args.kwargs
        assert kwargs.get("scope") == ProxyScope.BUILD
        assert kwargs.get("clearance_origin") == "https://accounts.x.ai/"


class TestProbeBuildBodyRead:
    """curl_cffi stream semantics: non-streamed response → sync .text.

    Regression for the 2026-08-05 sweep: `await response.atext()` on a
    non-streamed curl_cffi response raises AssertionError (stream mode not
    enabled). The probe POST has no stream=True, so the body must be read via
    the sync `.text` property.
    """

    @pytest.mark.asyncio
    async def test_probe_reads_body_via_sync_text(self):
        from app.control.account.build_detect import _probe_build

        session = AsyncMock()
        resp = AsyncMock()
        resp.status_code = 401
        resp.text = '{"error":"invalid token"}'
        # Simulate real curl_cffi: atext() on a non-streamed response raises.
        async def _bad_atext():
            raise AssertionError("stream mode is not enabled.")

        resp.atext = _bad_atext
        session.post.return_value = resp
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)

        proxy = AsyncMock()
        lease = AsyncMock()
        lease.cf_cookies = ""
        proxy.acquire.return_value = lease

        with (
            patch(
                "app.dataplane.proxy.get_proxy_runtime",
                AsyncMock(return_value=proxy),
            ),
            patch(
                "app.dataplane.proxy.adapters.session.ResettableSession",
                return_value=session,
            ),
        ):
            status, body = await _probe_build("at-tok")

        assert status == 401
        assert '"invalid token"' in body
