"""Build account model catalog — remote discovery (port of Go cli/adapter.go).

Go ``ModelCatalogRemote``: each Build account advertises its own model list via
GET {BuildBase}/v1/models. The list is filtered (hidden entries dropped,
firstNonEmpty(id, model, modelId, _meta.*) identifier) and then normalized by
``NormalizeAccountModelCapabilities``: video 1.5 only for Super accounts,
Composer always appended for Build OAuth sessions. Static registry names
(grok-4.5 etc.) remain the superset; this module only *adds* account-level
models at list time.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

import orjson

from app.dataplane.reverse.runtime.endpoint_table import BUILD_BASE, BUILD_MODELS

# Go cli/video.go buildVideoModel + domain/model/reasoning.go GrokComposer25Fast.
BUILD_VIDEO_MODEL = "grok-imagine-video-1.5"
BUILD_COMPOSER_MODEL = "grok-composer-2.5-fast"

_MAX_MODELS_BODY_BYTES = 4 << 20  # Go listModelsAt io.LimitReader 4MiB


def normalize_account_model_capabilities(
    models: list[str],
    *,
    is_super: bool,
    is_build_oauth: bool,
) -> list[str]:
    """Port of Go ``NormalizeAccountModelCapabilities`` (cli/adapter.go:575).

    - drops ``buildVideoModel`` unless *is_super* (Free/Unknown remove video 1.5)
    - appends ``buildVideoModel`` for super accounts missing it
    - appends ``BUILD_COMPOSER_MODEL`` for Build OAuth sessions
    """
    seen: set[str] = set()
    result: list[str] = []
    for model in models:
        model = model.strip()
        if not model or model in seen:
            continue
        if model == BUILD_VIDEO_MODEL and not is_super:
            continue
        seen.add(model)
        result.append(model)
    if is_super and BUILD_VIDEO_MODEL not in seen:
        result.append(BUILD_VIDEO_MODEL)
    if is_build_oauth and BUILD_COMPOSER_MODEL not in seen:
        result.append(BUILD_COMPOSER_MODEL)
    return result


def parse_build_model_catalog(body: bytes) -> list[str]:
    """Port of Go ``listModelsAt`` parsing (cli/adapter.go:632-683).

    ``{"data": [{id, model, modelId, hidden, _meta{...}}]}`` → identifiers.
    Empty/hidden entries skipped; duplicates dropped.
    """
    try:
        payload = orjson.loads(body)
    except (orjson.JSONDecodeError, ValueError):
        return []
    raw_data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(raw_data, list):
        return []
    seen: set[str] = set()
    models: list[str] = []
    for item in raw_data:
        if not isinstance(item, dict):
            continue
        hidden = bool(item.get("hidden")) or bool(
            item.get("_meta", {}).get("hidden")
            if isinstance(item.get("_meta"), dict)
            else False
        )
        if hidden:
            continue
        identifier = _first_non_empty(
            item.get("id"),
            item.get("model"),
            item.get("modelId"),
            item.get("_meta", {}).get("model")
            if isinstance(item.get("_meta"), dict)
            else None,
            item.get("_meta", {}).get("modelId")
            if isinstance(item.get("_meta"), dict)
            else None,
        )
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        models.append(identifier)
    return models


def _first_non_empty(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


async def list_build_models(
    access_token: str,
    *,
    agent_id: str = "",
    timeout_s: float = 30.0,
    request_fn: Callable[..., Awaitable[tuple[int, bytes, dict[str, str]]]]
    | None = None,
) -> list[str]:
    """Fetch the account's remote model catalog.

    Default ``request_fn`` is a plain GET over the project HTTP transport;
    callers in the async request path may inject their own (session-bound)
    fetcher to reuse proxy/clearance machinery. Returns raw remote list
    (pre-normalization); caller applies ``normalize_account_model_capabilities``
    with the account's billing/tier context.
    """
    if request_fn is not None:
        try:
            status, body, _ = await request_fn(BUILD_MODELS, None)
        except Exception:
            return []
    else:
        # build_detect._probe_build pattern: ResettableSession + Build
        # headers, non-stream GET, sync response.content.
        from app.dataplane.proxy import get_proxy_runtime
        from app.dataplane.proxy.adapters.headers import build_build_headers
        from app.dataplane.proxy.adapters.session import (
            ResettableSession,
            build_session_kwargs,
        )

        try:
            proxy = await get_proxy_runtime()
            lease = await proxy.acquire(clearance_origin=BUILD_BASE)
            headers = build_build_headers(
                access_token=access_token,
                agent_id=agent_id,
                is_stream=False,
                is_trace=False,
            )
            session_kwargs = build_session_kwargs(lease=lease, disable_fingerprint=True)
            async with ResettableSession(**session_kwargs) as session:
                response = await session.get(
                    BUILD_MODELS, headers=headers, timeout=timeout_s
                )
                status = response.status_code
                body = response.content[:_MAX_MODELS_BODY_BYTES]
        except Exception:
            return []
    if status != 200:
        return []
    return parse_build_model_catalog(body)


# Go caches the catalog per account with an ETag; Python uses a short-TTL
# cache so /v1/models never blocks on per-request network calls.
_REMOTE_MODELS_TTL_S = 300.0
_remote_models_cache: dict[str, tuple[float, list[str]]] = {}


async def collect_build_remote_models(
    repo: Any,
    *,
    request_fn: Callable[..., Awaitable[tuple[int, bytes, dict[str, str]]]]
    | None = None,
) -> list[str]:
    """Remote Build model discovery across active build accounts.

    Merged, deduped model ids from every active ``pool == "build"`` account
    (Go ``Adapter.ListModels`` per account). Static registry names stay
    untouched; the caller merges this superset into the registry list.
    """
    from app.control.account.enums import AccountStatus
    from app.control.account.state_machine import derive_status

    snapshot = await repo.runtime_snapshot()
    records = [
        record
        for record in snapshot.items
        if record.pool == "build"
        and not record.is_deleted()
        and derive_status(record) == AccountStatus.ACTIVE
    ]
    merged: list[str] = []
    seen: set[str] = set()
    for record in records:
        for model in await _account_remote_models(record, request_fn=request_fn):
            if model not in seen:
                seen.add(model)
                merged.append(model)
    return merged


async def _account_remote_models(
    record: Any,
    *,
    request_fn: Callable[..., Awaitable[tuple[int, bytes, dict[str, str]]]]
    | None = None,
) -> list[str]:
    """Cached remote catalog for one build account (Go ETag-cache analog)."""
    now = time.monotonic()
    cached = _remote_models_cache.get(record.token)
    if cached is not None and now - cached[0] < _REMOTE_MODELS_TTL_S:
        return cached[1]
    models = await _fetch_account_remote_models(record, request_fn=request_fn)
    # ponytail: process-local cache — fine at workers=1; shard if multi-worker
    _remote_models_cache[record.token] = (now, models)
    return models


async def _fetch_account_remote_models(
    record: Any,
    *,
    request_fn: Callable[..., Awaitable[tuple[int, bytes, dict[str, str]]]]
    | None = None,
) -> list[str]:
    """One account: fetch catalog + billing, then normalize (Go ListModels).

    Billing failure degrades to free (video 1.5 dropped) — same as Go with
    an unknown tier. All enumerated ``grok_build`` accounts are OAuth-minted
    (sso_build auth_type="oauth"), so Composer is always part of the session
    contract.
    """
    ext = dict(record.ext or {})
    access_token = str(ext.get("build_access_token") or record.token)
    try:
        from app.dataplane.reverse.protocol.xai_billing import fetch_build_billing

        billing = await fetch_build_billing(access_token)
        is_super = billing.is_paid
    except Exception:
        is_super = False
    raw = await list_build_models(access_token, request_fn=request_fn)
    return normalize_account_model_capabilities(
        raw, is_super=is_super, is_build_oauth=True
    )
