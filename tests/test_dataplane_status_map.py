"""Dataplane status mapping regression tests.

Guards against the trap where ``STATUS_STR_TO_ID.get(..., ACTIVE)`` silently
falls back to ACTIVE for unknown statuses — a REAUTH_REQUIRED account would
then stay selectable in the runtime table.
"""

import pytest

from app.control.account.enums import AccountStatus
from app.control.account.models import AccountChangeSet, AccountRecord, RuntimeSnapshot
from app.control.account.quota_defaults import default_quota_set
from app.dataplane.account.sync import _record_to_slot_args, bootstrap
from app.dataplane.shared.enums import STATUS_STR_TO_ID, StatusId


def test_status_str_to_id_maps_reauth():
    assert STATUS_STR_TO_ID["reauth_required"] == int(StatusId.REAUTH_REQUIRED)


def test_record_to_slot_args_status():
    rec = AccountRecord(token="t-reauth", status=AccountStatus.REAUTH_REQUIRED)
    assert _record_to_slot_args(rec)["status_id"] == int(StatusId.REAUTH_REQUIRED)


class _FakeRepo:
    """Minimal repository double for bootstrap() — only runtime_snapshot used."""

    def __init__(self, items, revision=1):
        self.items = items
        self.revision = revision

    async def runtime_snapshot(self):
        return RuntimeSnapshot(revision=self.revision, items=self.items)

    async def scan_changes(self, revision, limit=5000):
        return AccountChangeSet(revision=self.revision, items=[], deleted_tokens=[])


@pytest.mark.asyncio
async def test_reauth_account_not_in_mode_available():
    # super pool → auto window defaults to 7200s > 0, so an ACTIVE account
    # would be added to mode_available; REAUTH_REQUIRED must not be.
    rec = AccountRecord(
        token="t-reauth",
        pool="super",
        status=AccountStatus.REAUTH_REQUIRED,
        quota=default_quota_set("super").to_dict(),
    )
    table = await bootstrap(_FakeRepo([rec]))

    assert "t-reauth" in table.token_by_idx
    idx = table.idx_by_token["t-reauth"]
    assert table.status_by_idx[idx] == int(StatusId.REAUTH_REQUIRED)

    available = {
        table.get_token(i) for bucket in table.mode_available.values() for i in bucket
    }
    assert "t-reauth" not in available
