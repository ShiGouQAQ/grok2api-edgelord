"""Admin model catalog — static registry served as a catalog with JSON overrides.

Port of Go ``backend/internal/transport/http/model/handler.go`` (GET /models,
/groups, /accounts, POST /models, /models/sync, PATCH /models/batch,
PATCH/DELETE /models/:id, DELETE /models). Python has no DB-backed catalog —
the static ``MODELS`` registry is the catalog and ``data/model_overrides.json``
carries admin enable/disable/tier deltas. Physical deletion is not supported
(501 stub) since static entries cannot be removed without a full DB migration.
"""

from __future__ import annotations

import orjson
from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Any

from app.control.model import overrides
from app.control.model.enums import Capability, ModeId
from app.control.model.registry import (
    get as get_spec,
    list_models_with_overrides,
)
from app.platform.errors import AppError, ErrorKind, ValidationError
from app.platform.logging.logger import logger
from . import get_repo

router = APIRouter(tags=["Admin - Models"])

_PROVIDER_BY_MODE = {
    ModeId.CONSOLE: "console",
    ModeId.BUILD: "grok_build",
}
_CAP_NAMES: dict[Capability, str] = {
    Capability.CHAT: "chat",
    Capability.IMAGE: "image",
    Capability.IMAGE_EDIT: "image_edit",
    Capability.VIDEO: "video",
    Capability.VOICE: "voice",
    Capability.ASSET: "asset",
    Capability.CONSOLE_CHAT: "console_chat",
    Capability.BUILD: "build",
}
_VALID_TIERS = {"basic", "super", "heavy"}
_VALID_PROVIDERS = {"grok_web", "console", "grok_build"}


class ModelPatchRequest(BaseModel):
    model: str
    enabled: bool | None = None
    tier: str | None = None


class ModelUpdateRequest(BaseModel):
    enabled: bool | None = None
    tier: str | None = None


class ModelBatchRequest(BaseModel):
    ids: list[str]
    enabled: bool


class ModelSyncRequest(BaseModel):
    provider: str = ""


def _json(data) -> Response:
    return Response(content=orjson.dumps(data), media_type="application/json")


def _provider_of(mode: ModeId) -> str:
    return _PROVIDER_BY_MODE.get(mode, "grok_web")


def _capabilities(cap: Capability) -> list[str]:
    return [name for bit, name in _CAP_NAMES.items() if cap & bit]


def _serialize(entry: dict[str, Any]) -> dict[str, Any]:
    """Admin view of one registry entry (``list_models_with_overrides`` shape)."""
    spec = get_spec(entry["model_name"])
    mode = spec.mode_id if spec is not None else entry["mode"]
    cap = entry["capability"]
    tier_override = overrides.tier(entry["model_name"])
    return {
        "public_id": entry["model_name"],
        "provider": _provider_of(mode),
        "upstream_model": entry["model_name"],
        "capability": _capabilities(cap),
        "enabled": entry["enabled"],
        "origin": "static",
        "tier": tier_override
        if tier_override is not None
        else (spec.tier.name.lower() if spec else "basic"),
        "mode": mode.to_api_str(),
        "account_ids": [],
        "binding_mode": False,
        "supported_accounts": 0,
        "synced_accounts": 0,
        "total_accounts": 0,
        "capability_known": True,
        "available": True,
        "last_synced_at": None,
    }


def _validate_tier(tier: str | None) -> str | None:
    if tier is None:
        return None
    tier = tier.strip().lower()
    if tier not in _VALID_TIERS:
        raise ValidationError(
            f"tier must be one of {sorted(_VALID_TIERS)}", param="tier"
        )
    return tier


def _require_known(model: str) -> None:
    if get_spec(model) is None:
        raise AppError(
            f"Model {model!r} not found in registry",
            kind=ErrorKind.VALIDATION,
            code="model_not_found",
            status=404,
        )


def _apply_delta(
    model: str, *, enabled: bool | None, tier: str | None
) -> dict[str, Any]:
    """Merge a delta into the override file; ``None`` removes the field (revert to static)."""
    _require_known(model)
    delta = dict(overrides.load().get(model, {}))
    if enabled is None:
        delta.pop("enabled", None)
    else:
        delta["enabled"] = enabled
    tier = _validate_tier(tier)
    if tier is None:
        delta.pop("tier", None)
    else:
        delta["tier"] = tier
    all_overrides = dict(overrides.load())
    if delta:
        all_overrides[model] = delta
    else:
        all_overrides.pop(model, None)
    overrides.save(all_overrides)
    logger.info("admin model override saved: model={} delta={}", model, delta)
    return delta


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/models")
async def list_models(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200, alias="pageSize"),
    search: str = "",
    provider: str | None = Query(None),
    status: str | None = Query(None),
    tier: str | None = Query(None),
):
    """List the catalog (static registry + overrides) with pagination."""
    if provider is not None and provider not in _VALID_PROVIDERS:
        raise ValidationError(
            f"provider must be one of {sorted(_VALID_PROVIDERS)}", param="provider"
        )
    items = [_serialize(e) for e in list_models_with_overrides()]
    if provider is not None:
        items = [i for i in items if i["provider"] == provider]
    if status in ("enabled", "disabled"):
        items = [i for i in items if i["enabled"] == (status == "enabled")]
    if tier is not None:
        items = [i for i in items if i["tier"] == tier]
    if search:
        needle = search.strip().lower()
        items = [i for i in items if needle in i["public_id"].lower()]
    total = len(items)
    start = (page - 1) * page_size
    return _json(
        {
            "items": items[start : start + page_size],
            "page": page,
            "pageSize": page_size,
            "total": total,
        }
    )


@router.get("/models/groups")
async def list_model_groups(
    request: Request,
    search: str = "",
    provider: str | None = Query(None),
):
    """Group the catalog by (provider, capability) with counts."""
    if provider is not None and provider not in _VALID_PROVIDERS:
        raise ValidationError(
            f"provider must be one of {sorted(_VALID_PROVIDERS)}", param="provider"
        )
    groups: dict[tuple[str, str], list[str]] = {}
    for entry in [_serialize(e) for e in list_models_with_overrides()]:
        if provider is not None and entry["provider"] != provider:
            continue
        if search and search.strip().lower() not in entry["public_id"].lower():
            continue
        for capability in entry["capability"]:
            groups.setdefault((entry["provider"], capability), []).append(
                entry["public_id"]
            )
    items = [
        {
            "key": f"{prov}:{cap}",
            "provider": prov,
            "capability": cap,
            "count": len(models),
            "models": models,
        }
        for (prov, cap), models in sorted(groups.items())
    ]
    return _json({"items": items, "total": len(items)})


@router.get("/models/accounts")
async def list_model_accounts(
    request: Request,
    provider: str | None = Query(None),
):
    """Per-model account support.

    ``provider=grok_build`` → remote-discovered Build model ids; other
    providers → each catalog model with a count of active accounts in the
    matching tier pool (from ``repo.runtime_snapshot``).
    """
    repo = get_repo(request)
    snapshot = await repo.runtime_snapshot()
    active = [r for r in snapshot.items if not r.is_deleted()]
    if provider == "grok_build":
        from app.control.account.build_models import collect_build_remote_models

        remote = await collect_build_remote_models(repo)
        build_count = sum(1 for r in active if r.pool == "build")
        items = [
            {
                "public_id": name,
                "provider": "grok_build",
                "supported_accounts": build_count,
                "total_accounts": build_count,
            }
            for name in remote
        ]
        return _json({"items": items})
    if provider is not None and provider not in _VALID_PROVIDERS:
        raise ValidationError(
            f"provider must be one of {sorted(_VALID_PROVIDERS)}", param="provider"
        )
    by_pool = {"basic": 0, "super": 0, "heavy": 0}
    for record in active:
        if record.pool in by_pool:
            by_pool[record.pool] += 1
    items = []
    for entry in [_serialize(e) for e in list_models_with_overrides()]:
        if provider is not None and entry["provider"] != provider:
            continue
        count = by_pool.get(entry["tier"], 0)
        items.append(
            {
                "public_id": entry["public_id"],
                "provider": entry["provider"],
                "tier": entry["tier"],
                "supported_accounts": count,
                "total_accounts": count,
            }
        )
    return _json({"items": items})


@router.post("/models")
async def toggle_model(req: ModelPatchRequest):
    """Enable/disable (and optionally re-tier) one model via the override file."""
    delta = _apply_delta(req.model, enabled=req.enabled, tier=req.tier)
    entry = {m["model_name"]: m for m in list_models_with_overrides()}[req.model]
    return _json({**_serialize(entry), "delta": delta})


@router.post("/models/sync")
async def sync_models(request: Request, req: ModelSyncRequest):
    """Re-trigger remote discovery for ``grok_build``; no-op for static providers."""
    provider = (req.provider or "grok_build").strip().lower()
    if provider != "grok_build":
        return _json(
            {
                "synced": 0,
                "models": [],
                "note": "静态注册表模型无需同步；仅 grok_build 支持远程发现",
            }
        )
    from app.control.account import build_models

    # Sync = force a fresh fetch: drop the per-account TTL cache, then discover.
    build_models._remote_models_cache.clear()
    repo = get_repo(request)
    remote = await build_models.collect_build_remote_models(repo)
    logger.info(
        "admin model sync completed: provider=grok_build synced={}", len(remote)
    )
    return _json({"synced": len(remote), "models": remote})


@router.patch("/models/batch")
async def batch_update_models(req: ModelBatchRequest):
    """Batch enable/disable via the override file."""
    if not req.ids:
        raise ValidationError("ids cannot be empty", param="ids")
    updated = 0
    all_overrides = dict(overrides.load())
    for model in req.ids:
        if get_spec(model) is None:
            continue
        delta = dict(all_overrides.get(model, {}))
        delta["enabled"] = req.enabled
        all_overrides[model] = delta
        updated += 1
    overrides.save(all_overrides)
    logger.info(
        "admin model batch updated: enabled={} requested={} updated={}",
        req.enabled,
        len(req.ids),
        updated,
    )
    return _json({"updated": updated})


@router.delete("/models")
async def delete_models():
    """Physical deletion requires a DB-backed catalog (Go model_repository)."""
    raise AppError(
        "静态注册表模型不支持物理删除；需先迁移到完整 DB 目录（Go model_repository）",
        kind=ErrorKind.SERVER,
        code="model_delete_not_supported",
        status=501,
    )


@router.patch("/models/{model_id}")
async def update_model(model_id: str, req: ModelUpdateRequest):
    """Update one model (enable/disable, tier override). ``None`` reverts to static."""
    delta = _apply_delta(model_id, enabled=req.enabled, tier=req.tier)
    entry = {m["model_name"]: m for m in list_models_with_overrides()}[model_id]
    return _json({**_serialize(entry), "delta": delta})


@router.delete("/models/{model_id}")
async def delete_model(model_id: str):
    """Physical deletion requires a DB-backed catalog (Go model_repository)."""
    raise AppError(
        "静态注册表模型不支持物理删除；需先迁移到完整 DB 目录（Go model_repository）",
        kind=ErrorKind.SERVER,
        code="model_delete_not_supported",
        status=501,
    )
