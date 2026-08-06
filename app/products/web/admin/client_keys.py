"""Client key administration endpoints (port of Go clientkey handler).

Endpoints mirror the Go surface: list/create, reveal secret, update/delete
single, batch enable/delete.  All under the ``/admin/api`` prefix with the
``verify_admin_key`` guard applied by the parent router.
"""

from typing import Any

import orjson
from fastapi import APIRouter, Request, Body, Path, Query
from fastapi.responses import Response

from app.platform.errors import AppError, ErrorKind, ValidationError

from app.control.clientkey.service import (
    ClientKeyError,
    ConflictError,
    CreateInput,
    InvalidInputError,
    NotFoundError,
    parse_rfc3339_ms,
    resolve_scopes,
)
from app.platform.runtime.clock import now_ms

router = APIRouter(tags=["Admin - Client Keys"])

_TAG = "Admin - Client Keys"


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


def _iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    from datetime import datetime, timezone

    return (
        datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _key_dto(key) -> dict[str, Any]:
    return {
        "id": str(key.id),  # Go: `json:"id,string"`
        "name": key.name,
        "prefix": key.prefix,
        "enabled": key.enabled,
        "expiresAt": _iso(key.expires_at),
        "rpmLimit": key.rpm_limit,
        "maxConcurrent": key.max_concurrent,
        "billingLimitUsdTicks": key.billing_limit_usd_ticks,
        "billedUsageUsdTicks": key.billed_usage_usd_ticks,
        "allowModelAliases": key.allow_model_aliases,
        "allowedModelIds": key.allowed_model_ids,
        "providerScope": key.provider_scope or ["all"],
        "tierScope": key.tier_scope or ["all"],
        "lastUsedAt": _iso(key.last_used_at),
    }


def _client_keys(request) -> Any:
    repo = getattr(request.app.state, "client_keys", None)
    if repo is None:
        raise AppError(
            "Client key store not initialised",
            kind=ErrorKind.SERVER,
            code="client_keys_not_initialised",
            status=503,
        )
    return repo


def _service(request) -> Any:
    svc = getattr(request.app.state, "client_key_service", None)
    if svc is None:
        raise AppError(
            "Client key service not initialised",
            kind=ErrorKind.SERVER,
            code="client_keys_not_initialised",
            status=503,
        )
    return svc


def _map_error(exc: ClientKeyError, fallback: str) -> AppError:
    if isinstance(exc, InvalidInputError):
        return AppError(
            exc.message, kind=ErrorKind.VALIDATION, code="invalidRequest", status=400
        )
    if isinstance(exc, NotFoundError):
        return AppError(
            exc.message, kind=ErrorKind.SERVER, code="clientKeyNotFound", status=404
        )
    if isinstance(exc, ConflictError):
        return AppError(
            exc.message, kind=ErrorKind.SERVER, code="clientKeyConflict", status=409
        )
    return AppError(
        exc.message or "客户端 Key 操作失败",
        kind=ErrorKind.SERVER,
        code=fallback,
        status=500,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/client-keys", tags=[_TAG])
async def list_client_keys(
    request: Request,
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=200),
    search: str = "",
    status: str = "",
    sortBy: str = "updated_at",
    sortOrder: str = "desc",
):
    repo = _client_keys(request)
    items, total = await repo.list_keys(
        page=page,
        page_size=pageSize,
        search=search,
        status=status,
        sort_by=sortBy,
        sort_desc=sortOrder.lower() != "asc",
    )
    return Response(
        content=orjson.dumps(
            {
                "items": [_key_dto(k) for k in items],
                "page": page,
                "pageSize": pageSize,
                "total": total,
            }
        ),
        media_type="application/json",
    )


@router.post("/client-keys", tags=[_TAG], status_code=201)
async def create_client_key(request: Request, payload: dict[str, Any] = Body(...)):
    svc = _service(request)
    try:
        expires_at = parse_rfc3339_ms(str(payload.get("expiresAt") or ""))
        provider_scope, tier_scope = resolve_scopes(
            payload.get("providerScope") or [],
            payload.get("tierScope") or [],
            payload.get("accountPool"),
        )
        result = await svc.create(
            CreateInput(
                name=payload.get("name", ""),
                enabled=payload.get("enabled", True),
                expires_at=expires_at,
                rpm_limit=payload.get("rpmLimit"),
                max_concurrent=payload.get("maxConcurrent"),
                billing_limit_usd_ticks=int(payload.get("billingLimitUsdTicks") or 0),
                allow_model_aliases=bool(payload.get("allowModelAliases", False)),
                allowed_model_ids=list(payload.get("allowedModelIds") or []),
                provider_scope=provider_scope,
                tier_scope=tier_scope,
            )
        )
    except ClientKeyError as exc:
        raise _map_error(exc, "clientKeyCreateFailed")
    return {"key": _key_dto(result.key), "secret": result.secret}


@router.patch("/client-keys/batch", tags=[_TAG])
async def batch_update_client_keys(request: Request, payload: dict[str, Any] = Body(...)):
    """Batch enable/disable — registered before the ``{key_id}`` routes so
    the literal ``batch`` path wins over the param route (FastAPI matches in
    registration order; unlike gin, static routes do not take precedence)."""
    repo = _client_keys(request)
    ids = [int(i) for i in payload.get("ids", []) if str(i).strip().isdigit()]
    if not ids:
        raise ValidationError("ids 不能为空", param="ids")
    updated = await repo.batch_set_enabled(ids, bool(payload.get("enabled", False)))
    return {"updated": updated}


@router.delete("/client-keys", tags=[_TAG])
async def batch_delete_client_keys(request: Request, payload: dict[str, Any] = Body(...)):
    """Batch delete — registered before the ``{key_id}`` routes (see batch
    update for the FastAPI route-order rationale)."""
    repo = _client_keys(request)
    ids = [int(i) for i in payload.get("ids", []) if str(i).strip().isdigit()]
    if not ids:
        raise ValidationError("ids 不能为空", param="ids")
    deleted = await repo.batch_delete(ids)
    return {"deleted": deleted}


@router.get("/client-keys/{key_id}/secret", tags=[_TAG])
async def reveal_client_key_secret(request: Request, key_id: int = Path(...)):
    svc = _service(request)
    try:
        key = await svc.get(key_id)
    except ClientKeyError as exc:
        raise _map_error(exc, "clientKeySecretReadFailed")
    return Response(
        content=orjson.dumps({"secret": key.secret}),
        media_type="application/json",
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.patch("/client-keys/{key_id}", tags=[_TAG])
async def update_client_key(
    request: Request, key_id: int = Path(...), payload: dict[str, Any] = Body(...)
):
    svc = _service(request)
    try:
        expires_at = None
        clear_expires_at = False
        if payload.get("expiresAt") is not None:
            if payload["expiresAt"] == "":
                clear_expires_at = True
            else:
                expires_at = parse_rfc3339_ms(str(payload["expiresAt"]))
        key = await svc.update(
            key_id,
            name=payload.get("name"),
            enabled=payload.get("enabled"),
            expires_at=expires_at,
            clear_expires_at=clear_expires_at,
            rpm_limit=payload.get("rpmLimit"),
            max_concurrent=payload.get("maxConcurrent"),
            billing_limit_usd_ticks=payload.get("billingLimitUsdTicks"),
            allow_model_aliases=payload.get("allowModelAliases"),
            allowed_model_ids=payload.get("allowedModelIds"),
            provider_scope=payload.get("providerScope"),
            tier_scope=payload.get("tierScope"),
        )
    except ClientKeyError as exc:
        raise _map_error(exc, "clientKeyUpdateFailed")
    return _key_dto(key)


@router.delete("/client-keys/{key_id}", tags=[_TAG])
async def delete_client_key(request: Request, key_id: int = Path(...)):
    svc = _service(request)
    try:
        await svc.delete(key_id)
    except ClientKeyError as exc:
        raise _map_error(exc, "clientKeyDeleteFailed")
    return {"deleted": True}


__all__ = ["router", "_key_dto"]
