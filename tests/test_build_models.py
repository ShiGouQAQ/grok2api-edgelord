"""Tests for Build account model catalog discovery (port of Go cli/adapter.go)."""

import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import orjson
import pytest

from app.control.account.backends.local import LocalAccountRepository
from app.control.account.build_models import (
    BUILD_COMPOSER_MODEL,
    BUILD_VIDEO_MODEL,
    _remote_models_cache,
    collect_build_remote_models,
    list_build_models,
    normalize_account_model_capabilities,
    parse_build_model_catalog,
)
from app.control.account.commands import AccountUpsert
from app.dataplane.reverse.runtime.endpoint_table import BUILD_MODELS


@pytest.fixture(autouse=True)
def _clear_remote_cache():
    _remote_models_cache.clear()
    yield
    _remote_models_cache.clear()


def _catalog(*entries: dict[str, Any]) -> bytes:
    return orjson.dumps({"data": list(entries)})


def _build_account(token: str = "build-tok") -> AccountUpsert:
    return AccountUpsert(
        token=token,
        pool="build",
        provider="grok_build",
        ext={
            "build_access_token": "at-build",
            "build_refresh_token": "rt-build",
            "build_expires_at": 2**62,  # access token alive
        },
    )


async def _make_repo(*upserts: AccountUpsert) -> LocalAccountRepository:
    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "accounts.db"
    repo = LocalAccountRepository(db_path)
    await repo.initialize()
    if upserts:
        await repo.upsert_accounts(list(upserts))
    return repo


def _patch_billing(is_paid: bool = False):
    return patch(
        "app.dataplane.reverse.protocol.xai_billing.fetch_build_billing",
        AsyncMock(return_value=SimpleNamespace(is_paid=is_paid)),
    )


class TestParseBuildModelCatalog:
    """Go buildModelCatalogEntry.modelIdentifier: id → model → modelId → _meta."""

    def test_identifier_fallbacks_in_order(self):
        body = _catalog(
            {"id": "a", "model": "m", "modelId": "mid"},
            {"model": "b", "modelId": "mid-b"},
            {"modelId": "c"},
            {"_meta": {"model": "d"}},
            {"_meta": {"modelId": "e"}},
        )
        assert parse_build_model_catalog(body) == ["a", "b", "c", "d", "e"]

    def test_hidden_entries_dropped(self):
        # Go skips entries whose identifier is empty too (missing all shapes).
        body = _catalog(
            {"id": "visible"},
            {"id": "hidden-top", "hidden": True},
            {"id": "hidden-meta", "_meta": {"hidden": True}},
            {},
        )
        assert parse_build_model_catalog(body) == ["visible"]

    def test_dedup(self):
        body = _catalog(
            {"id": "same"},
            {"id": "same", "model": "other"},
            {"model": "same"},
        )
        assert parse_build_model_catalog(body) == ["same"]

    def test_empty_or_invalid_body(self):
        assert parse_build_model_catalog(b'{"data": []}') == []
        assert parse_build_model_catalog(b"not json") == []
        assert parse_build_model_catalog(b'{"data": "x"}') == []
        assert parse_build_model_catalog(b"") == []


class TestNormalizeAccountModelCapabilities:
    """Go NormalizeAccountModelCapabilities: video 1.5 super-only, composer OAuth."""

    def test_free_drops_video15(self):
        assert normalize_account_model_capabilities(
            ["grok-4.5", BUILD_VIDEO_MODEL],
            is_super=False,
            is_build_oauth=False,
        ) == ["grok-4.5"]

    def test_super_keeps_and_appends_video15(self):
        assert normalize_account_model_capabilities(
            ["grok-4.5", BUILD_VIDEO_MODEL],
            is_super=True,
            is_build_oauth=False,
        ) == ["grok-4.5", BUILD_VIDEO_MODEL]
        assert normalize_account_model_capabilities(
            ["grok-4.5"], is_super=True, is_build_oauth=False
        ) == ["grok-4.5", BUILD_VIDEO_MODEL]

    def test_oauth_appends_composer(self):
        assert normalize_account_model_capabilities(
            ["grok-4.5"], is_super=False, is_build_oauth=True
        ) == ["grok-4.5", BUILD_COMPOSER_MODEL]

    def test_strip_and_dedup(self):
        assert normalize_account_model_capabilities(
            [" grok-4.5 ", "grok-4.5", " ", ""],
            is_super=True,
            is_build_oauth=True,
        ) == ["grok-4.5", BUILD_VIDEO_MODEL, BUILD_COMPOSER_MODEL]


class TestListBuildModels:
    """list_build_models with an injected request_fn (no network)."""

    @pytest.mark.asyncio
    async def test_ok_body_parsed(self):
        body = _catalog({"id": "grok-4.5"}, {"id": BUILD_VIDEO_MODEL})
        fn = AsyncMock(return_value=(200, body, {}))
        models = await list_build_models("at-build", request_fn=fn)
        assert models == ["grok-4.5", BUILD_VIDEO_MODEL]
        fn.assert_awaited_once_with(BUILD_MODELS, None)

    @pytest.mark.asyncio
    async def test_non_200_returns_empty(self):
        fn = AsyncMock(return_value=(404, b"{}", {}))
        assert await list_build_models("at-build", request_fn=fn) == []

    @pytest.mark.asyncio
    async def test_request_fn_raises_returns_empty(self):
        fn = AsyncMock(side_effect=RuntimeError("boom"))
        assert await list_build_models("at-build", request_fn=fn) == []


class TestCollectBuildRemoteModels:
    """collect_build_remote_models: merged catalog across active build accounts."""

    @pytest.mark.asyncio
    async def test_merges_across_accounts_with_dedup(self):
        repo = await _make_repo(_build_account("a"), _build_account("b"))
        fn = AsyncMock(
            side_effect=[
                (200, _catalog({"id": "grok-4.5"}), {}),
                (200, _catalog({"id": "grok-4.5"}, {"id": "grok-4.5-b"}), {}),
            ]
        )
        with _patch_billing(is_paid=False):
            models = await collect_build_remote_models(repo, request_fn=fn)
        assert models == ["grok-4.5", BUILD_COMPOSER_MODEL, "grok-4.5-b"]

    @pytest.mark.asyncio
    async def test_super_account_keeps_video15(self):
        repo = await _make_repo(_build_account())
        fn = AsyncMock(return_value=(200, _catalog({"id": "grok-4.5"}), {}))
        with _patch_billing(is_paid=True):
            models = await collect_build_remote_models(repo, request_fn=fn)
        assert models == ["grok-4.5", BUILD_VIDEO_MODEL, BUILD_COMPOSER_MODEL]

    @pytest.mark.asyncio
    async def test_billing_failure_degrades_to_free(self):
        repo = await _make_repo(_build_account())
        fn = AsyncMock(return_value=(200, _catalog({"id": "grok-4.5"}), {}))
        with patch(
            "app.dataplane.reverse.protocol.xai_billing.fetch_build_billing",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            models = await collect_build_remote_models(repo, request_fn=fn)
        assert models == ["grok-4.5", BUILD_COMPOSER_MODEL]

    @pytest.mark.asyncio
    async def test_result_cached_within_ttl(self):
        repo = await _make_repo(_build_account())
        fn = AsyncMock(return_value=(200, _catalog({"id": "grok-4.5"}), {}))
        with _patch_billing(is_paid=False):
            await collect_build_remote_models(repo, request_fn=fn)
            await collect_build_remote_models(repo, request_fn=fn)
        assert fn.await_count == 1

    @pytest.mark.asyncio
    async def test_ignores_inactive_build_accounts(self):
        from app.control.account.commands import AccountPatch
        from app.control.account.enums import AccountStatus

        repo = await _make_repo(_build_account("active"), _build_account("expired"))
        await repo.patch_accounts(
            [AccountPatch(token="expired", status=AccountStatus.EXPIRED)]
        )
        fn = AsyncMock(return_value=(200, _catalog({"id": "grok-4.5"}), {}))
        with _patch_billing(is_paid=False):
            models = await collect_build_remote_models(repo, request_fn=fn)
        assert models == ["grok-4.5", BUILD_COMPOSER_MODEL]
        assert fn.await_count == 1
