"""Tests for SSO→Build conversion validation in the admin build-convert endpoint.

User decision: a minted Build token only counts as ``success`` after real
verification — non-empty access_token + unexpired JWT exp + smoke request to
api.x.ai/billing/usage returning 200.
"""

import base64
import json
import time
from unittest.mock import AsyncMock, patch

import orjson
import pytest

from app.control.account.build_refresh import compute_refresh_due_at
from app.control.account.models import AccountRecord
from app.control.account.sso_build import BuildCredentialSeed
from app.dataplane.reverse.protocol.xai_billing import BuildBilling
from app.platform.errors import UpstreamError
from app.products.web.admin.tokens import BuildConvertRequest, build_convert


def _jwt(payload: dict) -> str:
    """Build a JWT-shaped token with the given JSON payload (unverified)."""
    seg = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"h.{seg}.sig"


def _future_exp() -> int:
    return int(time.time()) + 7200


def _mock_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.upsert_accounts = AsyncMock()
    repo.get_accounts = AsyncMock(return_value=[])
    repo.patch_accounts = AsyncMock()
    return repo


async def _convert_one_response(sso_tokens: list[str], repo: AsyncMock) -> dict:
    """Call the endpoint and return its parsed JSON body."""
    resp = await build_convert(BuildConvertRequest(sso_tokens=sso_tokens), repo=repo)
    return orjson.loads(resp.body)


@pytest.mark.asyncio
async def test_convert_empty_access_token_fails():
    """Empty minted access_token must count as failed, never upserted."""
    repo = _mock_repo()
    creds = BuildCredentialSeed(access_token="", expires_in=3600)

    with patch(
        "app.control.account.sso_build.convert_sso_to_build",
        AsyncMock(return_value=creds),
    ):
        resp = await _convert_one_response(["sso-token-1"], repo)

    assert resp["success"] == 0
    assert resp["failed"] == 1
    repo.upsert_accounts.assert_not_called()


@pytest.mark.asyncio
async def test_convert_expired_jwt_fails():
    """Minted token whose JWT exp is in the past must count as failed."""
    repo = _mock_repo()
    creds = BuildCredentialSeed(
        access_token=_jwt({"exp": int(time.time()) - 60}),
        refresh_token="r",
        expires_in=3600,
    )

    with patch(
        "app.control.account.sso_build.convert_sso_to_build",
        AsyncMock(return_value=creds),
    ):
        resp = await _convert_one_response(["sso-token-1"], repo)

    assert resp["success"] == 0
    assert resp["failed"] == 1
    repo.upsert_accounts.assert_not_called()


@pytest.mark.asyncio
async def test_convert_smoke_200_succeeds():
    """Valid token + smoke 200 must count as success and store billing."""
    repo = _mock_repo()
    creds = BuildCredentialSeed(
        access_token=_jwt({"exp": _future_exp()}),
        refresh_token="r",
        id_token="id",
        expires_in=3600,
    )
    billing = BuildBilling(
        plan_code="free", plan_name="Free", used=10, monthly_limit=20
    )

    with (
        patch(
            "app.control.account.sso_build.convert_sso_to_build",
            AsyncMock(return_value=creds),
        ),
        patch(
            "app.dataplane.reverse.protocol.xai_billing.fetch_build_billing",
            AsyncMock(return_value=billing),
        ),
    ):
        resp = await _convert_one_response(["sso-token-1"], repo)

    assert resp["success"] == 1
    assert resp["failed"] == 0
    repo.upsert_accounts.assert_awaited_once()
    upsert = repo.upsert_accounts.await_args.args[0][0]
    ext = upsert.ext
    assert ext["build_billing"]["plan_code"] == "free"
    assert ext["build_billing"]["used"] == 10
    assert ext["build_access_token"] == creds.access_token


@pytest.mark.asyncio
async def test_convert_smoke_401_fails_and_marks_source():
    """Smoke 401 (credential_rejected) counts as failed and marks the SSO source."""
    repo = _mock_repo()
    creds = BuildCredentialSeed(
        access_token=_jwt({"exp": _future_exp()}),
        refresh_token="r",
        expires_in=3600,
    )
    exc = UpstreamError(
        "Build billing access denied: HTTP 401",
        status=401,
        credential_rejected=True,
        body="invalid-credentials",
    )

    with (
        patch(
            "app.control.account.sso_build.convert_sso_to_build",
            AsyncMock(return_value=creds),
        ),
        patch(
            "app.dataplane.reverse.protocol.xai_billing.fetch_build_billing",
            AsyncMock(side_effect=exc),
        ),
        patch(
            "app.control.account.invalid_credentials.mark_account_invalid_credentials",
            AsyncMock(return_value=True),
        ) as mock_mark,
    ):
        resp = await _convert_one_response(["sso-token-1"], repo)

    assert resp["success"] == 0
    assert resp["failed"] == 1
    repo.upsert_accounts.assert_not_called()
    mock_mark.assert_awaited_once()
    assert mock_mark.await_args is not None
    assert mock_mark.await_args.args[1] == "sso-token-1"
    assert mock_mark.await_args.args[2] is exc
    assert mock_mark.await_args.kwargs["source"] == "sso→build convert"


@pytest.mark.asyncio
async def test_convert_one_marks_account_on_device_precheck_rejected():
    """Device Flow precheck raises SSOCredentialRejected → source account marked.

    Mirrors Go markSSOCredentialRejected: an invalid SSO found at
    pre-validation must invalidate the source Web account, not be retried.
    """
    from app.control.account.sso_build import SSOCredentialRejected

    repo = _mock_repo()

    with (
        patch(
            "app.control.account.sso_build._mint_via_device_flow",
            AsyncMock(
                side_effect=SSOCredentialRejected(
                    "SSO token invalid or expired: redirected to sign-in"
                )
            ),
        ),
        patch(
            "app.control.account.sso_build._mint_via_pkce_cs",
            AsyncMock(side_effect=RuntimeError("PKCE must not run")),
        ),
        patch(
            "app.control.account.invalid_credentials.mark_account_invalid_credentials",
            AsyncMock(return_value=True),
        ) as mock_mark,
    ):
        resp = await _convert_one_response(["sso-token-1"], repo)

    assert resp["success"] == 0
    assert resp["failed"] == 1
    repo.upsert_accounts.assert_not_called()
    mock_mark.assert_awaited_once()
    assert mock_mark.await_args is not None
    assert mock_mark.await_args.args[1] == "sso-token-1"
    assert isinstance(mock_mark.await_args.args[2], SSOCredentialRejected)


@pytest.mark.asyncio
async def test_convert_valid_jwt_exp_used():
    """Present JWT exp wins over now + expires_in arithmetic."""
    repo = _mock_repo()
    exp = _future_exp()
    creds = BuildCredentialSeed(
        access_token=_jwt({"exp": exp}),
        refresh_token="r",
        expires_in=3600,
    )
    billing = BuildBilling(plan_code="free")

    with (
        patch(
            "app.control.account.sso_build.convert_sso_to_build",
            AsyncMock(return_value=creds),
        ),
        patch(
            "app.dataplane.reverse.protocol.xai_billing.fetch_build_billing",
            AsyncMock(return_value=billing),
        ),
    ):
        resp = await _convert_one_response(["sso-token-1"], repo)

    assert resp["success"] == 1
    upsert = repo.upsert_accounts.await_args.args[0][0]
    # Real exp wins: exp*1000, not now + expires_in*1000 (= 1h from now)
    assert upsert.ext["build_expires_at"] == exp * 1000
    # refresh_due_at is derived from the real exp
    expected_due = int(compute_refresh_due_at(exp, creds.access_token) * 1000)
    assert upsert.ext["build_refresh_due_at"] == expected_due


@pytest.mark.asyncio
async def test_convert_preserves_web_account_not_overwrite():
    """ "Copy, not overwrite": a minted Build account is upserted with its own
    token, while the original Web SSO account is kept and only linked via a
    build_linked_* patch — never deleted."""
    repo = _mock_repo()
    creds = BuildCredentialSeed(
        access_token=_jwt({"exp": _future_exp()}),
        refresh_token="r",
        id_token="id",
        expires_in=3600,
    )
    billing = BuildBilling(plan_code="free")
    web_record = AccountRecord(
        token="web-sso-token", pool="basic", provider="grok_web", ext={}
    )
    repo.get_accounts = AsyncMock(return_value=[web_record])
    repo.delete_accounts = AsyncMock()

    with (
        patch(
            "app.control.account.sso_build.convert_sso_to_build",
            AsyncMock(return_value=creds),
        ),
        patch(
            "app.dataplane.reverse.protocol.xai_billing.fetch_build_billing",
            AsyncMock(return_value=billing),
        ),
    ):
        resp = await _convert_one_response(["sso-token-1"], repo)

    assert resp["success"] == 1
    upsert = repo.upsert_accounts.await_args.args[0][0]
    assert upsert.token == creds.access_token
    assert upsert.token != "sso-token-1"
    assert upsert.pool == "build"
    assert upsert.provider == "grok_build"
    patch_args = repo.patch_accounts.await_args.args[0][0]
    assert patch_args.token == "web-sso-token"
    assert "build_linked_at" in patch_args.ext_merge
    assert patch_args.ext_merge["build_linked_token"] == creds.access_token[:16]
    repo.delete_accounts.assert_not_called()


@pytest.mark.asyncio
async def test_convert_smoke_failure_does_not_touch_web_account():
    """Smoke 401 must fail without creating a half-baked Build account and
    without patching/removing the original Web account."""
    repo = _mock_repo()
    creds = BuildCredentialSeed(
        access_token=_jwt({"exp": _future_exp()}),
        refresh_token="r",
        expires_in=3600,
    )
    exc = UpstreamError(
        "Build billing access denied: HTTP 401",
        status=401,
        credential_rejected=True,
        body="invalid-credentials",
    )
    repo.get_accounts = AsyncMock(
        return_value=[
            AccountRecord(
                token="web-sso-token", pool="basic", provider="grok_web", ext={}
            )
        ]
    )
    repo.delete_accounts = AsyncMock()

    with (
        patch(
            "app.control.account.sso_build.convert_sso_to_build",
            AsyncMock(return_value=creds),
        ),
        patch(
            "app.dataplane.reverse.protocol.xai_billing.fetch_build_billing",
            AsyncMock(side_effect=exc),
        ),
        patch(
            "app.control.account.invalid_credentials.mark_account_invalid_credentials",
            AsyncMock(return_value=True),
        ),
    ):
        resp = await _convert_one_response(["sso-token-1"], repo)

    assert resp["success"] == 0
    assert resp["failed"] == 1
    repo.upsert_accounts.assert_not_called()
    repo.patch_accounts.assert_not_called()
    repo.delete_accounts.assert_not_called()
