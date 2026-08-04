"""Regression test: concurrent quota decrements must not lose updates.

Two concurrent ``refresh_call_async`` calls on the same console account both
read ``remaining=20`` from the same record snapshot, both computed 19 and both
patched 19 — one decrement lost.  The per-token lock in
``AccountRefreshService`` serializes the read-modify-write so two concurrent
successful calls must end at ``remaining=18``.
"""

import asyncio

import pytest

from app.control.account.backends.local import LocalAccountRepository
from app.control.account.commands import AccountUpsert
from app.control.account.refresh import AccountRefreshService


async def _make_repo(tmp_path) -> LocalAccountRepository:
    """Create a fresh LocalAccountRepository with one console account."""
    repo = LocalAccountRepository(tmp_path / "accounts.db")
    await repo.initialize()
    await repo.upsert_accounts(
        [AccountUpsert(token="console-tok", pool="basic", provider="grok_console")]
    )
    return repo


@pytest.mark.asyncio
async def test_concurrent_refresh_decrements_atomically(tmp_path):
    """2 concurrent mode-5 refreshes on remaining=20 must end at 18, not 19."""
    repo = await _make_repo(tmp_path)
    svc = AccountRefreshService(repo)

    rec = (await repo.get_accounts(["console-tok"]))[0]
    win = rec.quota_set().get(5)
    assert win is not None
    assert win.remaining == 20

    # mode_id=5 (CONSOLE) is the local-managed decrement path — no upstream
    # fetch, so no network mocking is needed.  Without the per-token lock
    # both calls read remaining=20 and both write 19 (lost update).
    await asyncio.gather(
        svc.refresh_call_async("console-tok", 5),
        svc.refresh_call_async("console-tok", 5),
    )

    rec = (await repo.get_accounts(["console-tok"]))[0]
    win = rec.quota_set().get(5)
    assert win is not None
    assert win.remaining == 18
