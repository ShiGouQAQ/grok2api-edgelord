"""Console quota refresh integration — real upstream quota supersedes the
local simulator in the refresh flow (Go PR #853 port).

``fetch_console_usage`` is mocked at the dataplane seam (the refresh method
imports it lazily, so the module attribute patch intercepts it). Nothing here
touches the network; ``get_proxy_runtime`` is mocked per the CLAUDE.md
SSO→Build mint rule.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.control.account.backends.local import LocalAccountRepository
from app.control.account.commands import AccountUpsert
from app.control.account.enums import AccountStatus, QuotaSource
from app.control.account.models import QuotaWindow
from app.control.account.refresh import AccountRefreshService
from app.dataplane.reverse.protocol.xai_console_usage import (
    CONSOLE_PREDICTED_CHAT_RECOVERY_WINDOW_S,
    ConsoleClearanceRequiredError,
    ConsoleQuotaError,
    ConsoleUsageResult,
)
from app.platform.errors import UpstreamError

NOW_MS = 2_000_000_000_000

_FETCH_SEAM = "app.dataplane.reverse.protocol.xai_console_usage.fetch_console_usage"


@pytest.fixture(autouse=True)
def _no_real_mint_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLAUDE.md rule: any test that could trigger the SSO→Build mint path
    must mock ``get_proxy_runtime`` (a real Turnstile solve never returns in
    CI — see tests/test_sso_build.py autouse fixture)."""
    proxy = AsyncMock()
    proxy.acquire.return_value = None
    monkeypatch.setattr(
        "app.dataplane.proxy.get_proxy_runtime",
        AsyncMock(return_value=proxy),
    )


async def _make_repo(tmp_path) -> LocalAccountRepository:
    """Fresh LocalAccountRepository with one basic grok_console account."""
    repo = LocalAccountRepository(tmp_path / "accounts.db")
    await repo.initialize()
    await repo.upsert_accounts(
        [AccountUpsert(token="console-tok", pool="basic", provider="grok_console")]
    )
    return repo


def _usage_result(*, chat_remaining: int = 15) -> ConsoleUsageResult:
    """Mimic fetch_console_usage's parsed output (sibling semantics)."""
    chat_reset = (
        NOW_MS + CONSOLE_PREDICTED_CHAT_RECOVERY_WINDOW_S * 1000
        if chat_remaining == 0
        else None
    )

    def _win(
        remaining: int,
        total: int,
        *,
        window_seconds: int = 0,
        reset_at: int | None = None,
    ) -> QuotaWindow:
        return QuotaWindow(
            remaining=remaining,
            total=total,
            window_seconds=window_seconds,
            reset_at=reset_at,
            synced_at=NOW_MS,
            source=QuotaSource.REAL,
        )

    return ConsoleUsageResult(
        chat=_win(
            chat_remaining,
            20,
            window_seconds=CONSOLE_PREDICTED_CHAT_RECOVERY_WINDOW_S,
            reset_at=chat_reset,
        ),
        image=_win(10, 10),
        video=_win(3, 5),
        used={"chat": 20 - chat_remaining, "image": 0, "video": 2},
    )


def _patch_fetch(return_value=None, side_effect=None):
    mock = AsyncMock(
        return_value=return_value if return_value is not None else _usage_result()
    )
    if side_effect is not None:
        mock.side_effect = side_effect
    return patch(_FETCH_SEAM, mock)


@pytest.mark.asyncio
async def test_refresh_console_success_writes_real_windows(tmp_path):
    """Success: quota_console ← upstream chat window; image/video → ext; synced."""
    repo = await _make_repo(tmp_path)
    svc = AccountRefreshService(repo)
    record = (await repo.get_accounts(["console-tok"]))[0]

    with _patch_fetch(return_value=_usage_result(chat_remaining=15)):
        result = await svc._refresh_one(record, apply_fallback=True)

    assert result.checked == 1
    assert result.refreshed == 1
    assert result.failed == 0

    rec = (await repo.get_accounts(["console-tok"]))[0]
    console = rec.quota_set().console
    assert console is not None
    assert console.remaining == 15
    assert console.total == 20
    assert console.window_seconds == CONSOLE_PREDICTED_CHAT_RECOVERY_WINDOW_S
    assert console.usage_percent == 25.0
    assert console.predicted is False
    assert console.source == QuotaSource.REAL
    assert console.synced_at == NOW_MS

    ext = rec.ext or {}
    image = QuotaWindow.from_dict(ext["console_quota_image"])
    video = QuotaWindow.from_dict(ext["console_quota_video"])
    assert image.window_seconds == 0
    assert image.reset_at is None
    assert image.predicted is False
    assert image.remaining == 10
    assert video.remaining == 3
    assert video.total == 5
    assert video.predicted is False

    assert rec.last_sync_at is not None
    assert rec.status == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_refresh_console_exhausted_chat_writes_predicted_recovery(tmp_path):
    """remaining==0 → predicted window with reset_at = fetch time + 24h."""
    repo = await _make_repo(tmp_path)
    svc = AccountRefreshService(repo)
    record = (await repo.get_accounts(["console-tok"]))[0]

    with _patch_fetch(return_value=_usage_result(chat_remaining=0)):
        result = await svc._refresh_one(record, apply_fallback=True)

    assert result.refreshed == 1
    console = (await repo.get_accounts(["console-tok"]))[0].quota_set().console
    assert console is not None
    assert console.remaining == 0
    assert console.predicted is True
    assert console.usage_percent == 100.0
    assert console.reset_at == NOW_MS + CONSOLE_PREDICTED_CHAT_RECOVERY_WINDOW_S * 1000


@pytest.mark.asyncio
async def test_refresh_console_credential_rejected_marks_reauth(tmp_path):
    """401 credential_rejected → existing credential-failure path (REAUTH for
    SSO-class console accounts), never EXPIRED."""
    repo = await _make_repo(tmp_path)
    svc = AccountRefreshService(repo)
    record = (await repo.get_accounts(["console-tok"]))[0]
    exc = UpstreamError(
        "Console usage rejected: 401", status=401, credential_rejected=True
    )

    with _patch_fetch(side_effect=exc):
        result = await svc._refresh_one(record, apply_fallback=True)

    assert result.expired == 1
    rec = (await repo.get_accounts(["console-tok"]))[0]
    assert rec.status == AccountStatus.REAUTH_REQUIRED
    assert rec.status != AccountStatus.EXPIRED
    assert rec.state_reason == str(exc)


@pytest.mark.asyncio
async def test_refresh_console_clearance_required_is_transient(tmp_path):
    """403 non-definitive (ConsoleClearanceRequiredError) → account untouched,
    transient failure counted, local simulator kept."""
    repo = await _make_repo(tmp_path)
    svc = AccountRefreshService(repo)
    record = (await repo.get_accounts(["console-tok"]))[0]

    with _patch_fetch(
        side_effect=ConsoleClearanceRequiredError(
            "Console usage returned 403",
            status=403,
            body="cf-challenge",
            invalidate_clearance=True,
        )
    ):
        result = await svc._refresh_one(record, apply_fallback=True)

    assert result.failed == 1
    rec = (await repo.get_accounts(["console-tok"]))[0]
    assert rec.status == AccountStatus.ACTIVE
    console = rec.quota_set().console
    assert console is not None
    assert console.remaining == 20  # local simulator untouched
    assert console.window_seconds == 3600
    assert console.source == QuotaSource.DEFAULT


@pytest.mark.asyncio
async def test_refresh_console_quota_error_keeps_local_fallback(tmp_path):
    """Malformed payload (ConsoleQuotaError) → transient; local simulator kept."""
    repo = await _make_repo(tmp_path)
    svc = AccountRefreshService(repo)
    record = (await repo.get_accounts(["console-tok"]))[0]

    with _patch_fetch(
        side_effect=ConsoleQuotaError("Console usage response missing quotas")
    ):
        result = await svc._refresh_one(record, apply_fallback=True)

    assert result.failed == 1
    rec = (await repo.get_accounts(["console-tok"]))[0]
    assert rec.status == AccountStatus.ACTIVE
    console = rec.quota_set().console
    assert console is not None
    assert console.remaining == 20
    assert console.source == QuotaSource.DEFAULT


@pytest.mark.asyncio
async def test_refresh_console_transport_error_keeps_local_fallback(tmp_path):
    """Transport failure (502 UpstreamError) → transient; local simulator kept."""
    repo = await _make_repo(tmp_path)
    svc = AccountRefreshService(repo)
    record = (await repo.get_accounts(["console-tok"]))[0]

    with _patch_fetch(
        side_effect=UpstreamError("Console usage fetch failed", status=502)
    ):
        result = await svc._refresh_one(record, apply_fallback=True)

    assert result.failed == 1
    rec = (await repo.get_accounts(["console-tok"]))[0]
    assert rec.status == AccountStatus.ACTIVE
    console = rec.quota_set().console
    assert console is not None
    assert console.remaining == 20
    assert console.source == QuotaSource.DEFAULT


@pytest.mark.asyncio
async def test_refresh_console_429_rate_limited_keeps_account(tmp_path):
    """429 → rate_limited counted; account stays active; local fallback kept."""
    repo = await _make_repo(tmp_path)
    svc = AccountRefreshService(repo)
    record = (await repo.get_accounts(["console-tok"]))[0]

    with _patch_fetch(
        side_effect=UpstreamError("Console usage returned 429", status=429)
    ):
        result = await svc._refresh_one(record, apply_fallback=True)

    assert result.rate_limited == 1
    assert result.failed == 1
    rec = (await repo.get_accounts(["console-tok"]))[0]
    assert rec.status == AccountStatus.ACTIVE
    console = rec.quota_set().console
    assert console is not None
    assert console.remaining == 20
    assert console.source == QuotaSource.DEFAULT
