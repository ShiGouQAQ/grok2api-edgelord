"""Tests for build-pool routing fix (POOL_STR_TO_ID + ModeId.BUILD columns).

Build OAuth accounts must live in their own pool (id 3) with quota_build
windows, and build-mode models must only select that pool — otherwise a
build request is served by grok_web accounts and fires cli-chat-proxy with
an SSO cookie (2026-08-05 production 14×429 rate-limits burst).
"""

import array
from unittest.mock import MagicMock

import pytest

from app.dataplane.account.table import AccountRuntimeTable, make_empty_table
from app.dataplane.shared.enums import (
    ALL_MODE_IDS,
    ModeId,
    POOL_ID_TO_STR,
    POOL_STR_TO_ID,
    PoolId,
)


class TestPoolMapping:
    def test_build_pool_id_is_3(self):
        assert int(PoolId.BUILD) == 3

    def test_pool_str_to_id_has_build(self):
        assert POOL_STR_TO_ID["build"] == 3

    def test_pool_id_to_str_roundtrip(self):
        assert POOL_ID_TO_STR[3] == "build"

    def test_all_mode_ids_contains_build(self):
        assert 6 in ALL_MODE_IDS


class TestBuildColumns:
    def _table_with_build_slot(self) -> AccountRuntimeTable:
        t = make_empty_table()
        t._append_slot(
            token="build-tok",
            pool_id=3,
            status_id=0,
            quota_auto=-1,
            quota_fast=-1,
            quota_expert=-1,
            quota_heavy=-1,
            quota_grok_4_3=-1,
            quota_console=-1,
            quota_build=100,
            total_auto=0,
            total_fast=0,
            total_expert=0,
            total_heavy=0,
            total_grok_4_3=0,
            total_console=0,
            total_build=100,
            window_auto=0,
            window_fast=0,
            window_expert=0,
            window_heavy=0,
            window_grok_4_3=0,
            window_console=0,
            window_build=7200,
            reset_auto=0,
            reset_fast=0,
            reset_expert=0,
            reset_heavy=0,
            reset_grok_4_3=0,
            reset_console=0,
            reset_build=0,
            health=1.0,
            last_use_s=0,
            last_fail_s=0,
            fail_count=0,
            tags=[],
        )
        return t

    def test_build_quota_column_maps_mode_6(self):
        t = self._table_with_build_slot()
        assert t._quota_col(6)[0] == 100
        assert t._total_col(6)[0] == 100
        assert t._window_col(6)[0] == 7200

    def test_build_slot_indexed_in_mode_available(self):
        t = self._table_with_build_slot()
        assert 0 in t.mode_available[(3, 6)]

    def test_build_slot_not_in_web_pool_bucket(self):
        t = self._table_with_build_slot()
        assert (0, 1) not in t.mode_available
        assert (1, 0) not in t.mode_available


class TestSpecPoolCandidates:
    def test_build_model_targets_only_build_pool(self):
        from app.control.model.enums import ModeId as CtrlModeId
        from app.control.model.registry import resolve

        spec = resolve("grok-4.5")
        assert spec.mode_id == CtrlModeId.BUILD
        assert spec.pool_candidates() == (3,)

    def test_build_mini_targets_only_build_pool(self):
        from app.control.model.registry import resolve

        assert resolve("grok-4.5-mini").pool_candidates() == (3,)

    def test_console_model_unaffected(self):
        from app.control.model.registry import resolve

        spec = resolve("grok-4.20-0309-reasoning-console")
        assert spec.mode_id.value != 6
        assert 3 not in spec.pool_candidates()
