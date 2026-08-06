"""Admin token CRUD — list, import, delete, replace pool.

Performance notes:
  - DI-injected repo (no try/except per call)
  - orjson direct output (bypasses stdlib json)
  - Quota dict: zero deserialization — reads r.quota directly
  - Import refresh: reuses app.state.refresh_service singleton
"""

import asyncio
import hashlib
import re
from typing import TYPE_CHECKING

import aiohttp

import orjson
from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, RootModel

from app.platform.errors import AppError, ErrorKind, UpstreamError, ValidationError
from app.platform.config.snapshot import get_config
from app.platform.logging.logger import logger
from app.platform.runtime.clock import now_ms
from app.control.account.commands import (
    AccountPatch,
    AccountUpsert,
    BulkReplacePoolCommand,
    ListAccountsQuery,
)
from app.control.account.provider_infer import infer_provider
from app.dataplane.proxy.adapters.session import normalize_proxy_url
from app.control.account.enums import AccountStatus
from app.control.account.state_machine import is_manageable
from .batch import _coerce_provider, _validate_provider

if TYPE_CHECKING:
    from app.control.account.refresh import AccountRefreshService
    from app.control.account.repository import AccountRepository

from . import get_refresh_svc, get_repo

router = APIRouter(tags=["Admin - Tokens"])
_background_tasks: set[asyncio.Task] = set()

# ---------------------------------------------------------------------------
# Token sanitisation
# ---------------------------------------------------------------------------

_TOKEN_TRANS = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u00a0": " ",
        "\u2007": " ",
        "\u202f": " ",
        "\u200b": "",
        "\u200c": "",
        "\u200d": "",
        "\ufeff": "",
    }
)
_STRIP_RE = re.compile(r"\s+")


def _sanitize(value: str) -> str:
    tok = str(value or "").translate(_TOKEN_TRANS)
    tok = _STRIP_RE.sub("", tok)
    if tok.startswith("sso="):
        tok = tok[4:]
    return tok.encode("ascii", errors="ignore").decode("ascii")


def strip_bom(text: str) -> str:
    """Strip UTF-8 BOM from text if present.

    Port of 83bf4f4: BOM in JSON/JSONL imports across providers.
    """
    return text.lstrip("\ufeff")


def _mask(token: str) -> str:
    return f"{token[:8]}...{token[-8:]}" if len(token) > 20 else token


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ReplacePoolRequest(BaseModel):
    pool: str
    tokens: list[str]
    tags: list[str] = []


class AddTokensRequest(BaseModel):
    tokens: list[str]
    pool: str = "basic"
    tags: list[str] = []


class EditTokenRequest(BaseModel):
    old_token: str
    token: str
    pool: str = "basic"


class ToggleTokenDisabledRequest(BaseModel):
    token: str
    disabled: bool


class ToggleTokensDisabledRequest(BaseModel):
    tokens: list[str]
    disabled: bool


class TokenImportItem(BaseModel):
    token: str
    tags: list[str] = []


class SaveTokensRequest(RootModel[dict[str, list[str | TokenImportItem]]]):
    """Bulk-save payload keyed by pool name."""


# ---------------------------------------------------------------------------
# Serialisation — zero-copy quota extraction
# ---------------------------------------------------------------------------


def _quota_brief(q: dict) -> dict:
    """Extract {auto, fast, expert, heavy, console} with only remaining/total from stored quota dict."""
    out = {}
    for mode in ("auto", "fast", "expert", "heavy", "console", "build"):
        v = q.get(mode)
        if isinstance(v, dict):
            out[mode] = {
                "remaining": int(v.get("remaining", 0) or 0),
                "total": int(v.get("total", 0) or 0),
            }
    return out


def _serialize_record(r) -> dict:
    ext = getattr(r, "ext", None) or {}
    billing = ext.get("build_billing") if isinstance(ext, dict) else None
    build_link = None
    if isinstance(ext, dict):
        if ext.get("build_linked_at"):
            build_link = {
                "linked_at": ext["build_linked_at"],
                "linked_token": ext.get("build_linked_token", ""),
            }
        elif ext.get("converted_from_token"):
            build_link = {
                "converted_from": ext["converted_from_token"],
                "converted_at": ext.get("converted_at", 0),
            }
    return {
        "token": r.token,
        "pool": r.pool or "basic",
        "provider": r.provider or "grok_web",
        "status": r.status,
        "state_reason": getattr(r, "state_reason", None),
        "last_fail_reason": getattr(r, "last_fail_reason", None),
        "quota": _quota_brief(r.quota) if isinstance(r.quota, dict) else {},
        "build_billing": billing,
        "build_link": build_link,
        "use_count": r.usage_use_count or 0,
        "fail_count": r.usage_fail_count or 0,
        "last_used_at": r.last_use_at,
        "tags": r.tags or [],
    }


def _json(data) -> Response:
    """orjson fast-path response."""
    return Response(content=orjson.dumps(data), media_type="application/json")


def _fire_and_forget(coro) -> asyncio.Task:
    # Keep a strong reference so import maintenance tasks cannot disappear before completion.
    task = asyncio.create_task(coro)
    _background_tasks.add(task)

    def _cleanup(done: asyncio.Task) -> None:
        _background_tasks.discard(done)
        if done.cancelled():
            return
        if exc := done.exception():
            logger.warning(
                "admin background task failed: error_type={}", type(exc).__name__
            )

    task.add_done_callback(_cleanup)
    return task


def _schedule_auto_nsfw(
    repo: "AccountRepository",
    tokens: list[str],
    *,
    enabled: bool,
) -> None:
    if not tokens or not enabled:
        return
    unique_tokens = list(dict.fromkeys(tokens))
    _fire_and_forget(_enable_nsfw_imported(repo, unique_tokens))


async def _list_all_records(repo: "AccountRepository") -> list:
    items: list = []
    page_num = 1
    while True:
        page = await repo.list_accounts(
            ListAccountsQuery(page=page_num, page_size=2000)
        )
        items.extend(page.items)
        if page_num >= page.total_pages or not page.items:
            break
        page_num += 1
    return items


async def _list_token_payloads(repo: "AccountRepository") -> list[dict]:
    fast_list = getattr(repo, "list_token_payloads", None)
    if callable(fast_list):
        return await fast_list()
    return [_serialize_record(r) for r in await _list_all_records(repo)]


def _matches_provider_payload(item: dict, provider: object) -> bool:
    p = _coerce_provider(provider)
    if p is None:
        return True
    return (item.get("provider") or "grok_web") == p


async def _list_invalid_tokens(
    repo: "AccountRepository", provider: object = None
) -> list[str]:
    if _coerce_provider(provider) is None:
        fast_list = getattr(repo, "list_invalid_tokens", None)
        if callable(fast_list):
            return await fast_list()
    return [
        item["token"]
        for item in await _list_token_payloads(repo)
        if _matches_provider_payload(item, provider)
        and item.get("status")
        not in (
            AccountStatus.ACTIVE.value,
            AccountStatus.COOLING.value,
            AccountStatus.DISABLED.value,
            AccountStatus.REAUTH_REQUIRED.value,
        )
        and not (
            item.get("status") == AccountStatus.EXPIRED.value
            and item.get("state_reason") == "console_429_threshold_exceeded"
        )
    ]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/tokens")
async def list_tokens(repo: "AccountRepository" = Depends(get_repo)):
    """Return flat token list."""
    return _json({"tokens": await _list_token_payloads(repo)})


@router.post("/tokens")
async def save_tokens(
    req: SaveTokensRequest,
    auto_nsfw: bool = Query(False),
    repo: "AccountRepository" = Depends(get_repo),
    refresh_svc: "AccountRefreshService" = Depends(get_refresh_svc),
):
    """Full pool replace — accepts {pool_name: [token_objects]} dict."""
    total_upserted = 0
    all_tokens: list[str] = []

    for pool_name, items in req.root.items():
        upserts = []
        for item in items:
            td = {"token": item} if isinstance(item, str) else item.model_dump()
            token_val = _sanitize(td.get("token", ""))
            if not token_val:
                continue
            upserts.append(
                AccountUpsert(
                    token=token_val,
                    pool=pool_name,
                    tags=td.get("tags") or [],
                    provider=infer_provider(token_val),
                )
            )
        if upserts:
            await repo.replace_pool(
                BulkReplacePoolCommand(pool=pool_name, upserts=upserts)
            )
            all_tokens.extend(u.token for u in upserts)
            total_upserted += len(upserts)

    logger.info("admin tokens saved across pools: saved_count={}", total_upserted)
    if all_tokens:
        _fire_and_forget(
            _refresh_then_auto_nsfw(
                refresh_svc,
                repo,
                all_tokens,
                auto_nsfw_enabled=auto_nsfw,
            )
        )
    return _json({"status": "success", "count": total_upserted})


@router.post("/tokens/add")
async def add_tokens(
    req: AddTokensRequest,
    auto_nsfw: bool = Query(False),
    repo: "AccountRepository" = Depends(get_repo),
    refresh_svc: "AccountRefreshService" = Depends(get_refresh_svc),
):
    requested_pool = (req.pool or "basic").strip().lower()

    # Deduplicate and sanitize input
    cleaned: list[str] = []
    seen: set[str] = set()
    for token in req.tokens:
        tok = _sanitize(token)
        if tok and tok not in seen:
            seen.add(tok)
            cleaned.append(tok)
    if not cleaned:
        raise ValidationError("No valid tokens provided", param="tokens")

    # Only upsert tokens that are not already active — avoids overwriting quota/status.
    # Soft-deleted tokens are treated as non-existing so they can be restored.
    existing = {r.token for r in await repo.get_accounts(cleaned) if not r.is_deleted()}
    new_tokens = [t for t in cleaned if t not in existing]

    if not new_tokens:
        return _json({"status": "success", "count": 0, "skipped": len(cleaned)})

    upserts = [
        AccountUpsert(
            token=t,
            pool=requested_pool,
            tags=req.tags,
            provider=infer_provider(t),
        )
        for t in new_tokens
    ]
    result = await repo.upsert_accounts(upserts)
    logger.info(
        "admin tokens added: pool={} added_count={} skipped_count={}",
        requested_pool,
        len(new_tokens),
        len(existing),
    )

    _fire_and_forget(
        _refresh_then_auto_nsfw(
            refresh_svc,
            repo,
            new_tokens,
            auto_nsfw_enabled=auto_nsfw,
        )
    )

    return _json(
        {
            "status": "success",
            "count": result.upserted or len(new_tokens),
            "skipped": len(existing),
        }
    )


@router.delete("/tokens")
async def delete_tokens(
    tokens: list[str] = Body(...),
    repo: "AccountRepository" = Depends(get_repo),
):
    cleaned = [t for t in (_sanitize(t) for t in tokens) if t]
    if not cleaned:
        raise ValidationError("No valid tokens provided", param="tokens")
    await repo.delete_accounts(cleaned)
    logger.info("admin tokens deleted: deleted_count={}", len(cleaned))
    return _json({"deleted": len(cleaned)})


@router.delete("/tokens/invalid")
async def delete_invalid_tokens(
    provider: str | None = Query(None),
    repo: "AccountRepository" = Depends(get_repo),
):
    _validate_provider(provider)
    tokens = await _list_invalid_tokens(repo, provider)

    if not tokens:
        return _json({"deleted": 0})

    await repo.delete_accounts(tokens)
    logger.info("admin invalid tokens deleted: deleted_count={}", len(tokens))
    return _json({"deleted": len(tokens)})


@router.put("/tokens/edit")
async def edit_token(
    req: EditTokenRequest,
    repo: "AccountRepository" = Depends(get_repo),
):
    old_token = _sanitize(req.old_token)
    new_token = _sanitize(req.token)
    pool = (req.pool or "basic").strip().lower()

    if not old_token or not new_token:
        raise ValidationError("Token is required", param="token")

    records = await repo.get_accounts([old_token])
    if not records:
        raise AppError(
            "Account not found",
            kind=ErrorKind.VALIDATION,
            code="account_not_found",
            status=404,
        )
    record = records[0]

    if old_token != new_token:
        existing = await repo.get_accounts([new_token])
        if existing:
            raise AppError(
                "Target token already exists",
                kind=ErrorKind.VALIDATION,
                code="token_conflict",
                status=409,
            )

    await repo.upsert_accounts(
        [
            AccountUpsert(
                token=new_token,
                pool=pool,
                tags=record.tags,
                provider=infer_provider(new_token) or record.provider,
                ext=record.ext,
            )
        ]
    )

    if old_token == new_token:
        logger.info("admin token updated: token={} pool={}", _mask(new_token), pool)
        return _json({"status": "success", "token": new_token, "pool": pool})

    qs = record.quota_set()
    await repo.patch_accounts(
        [
            AccountPatch(
                token=new_token,
                status=record.status,
                tags=record.tags,
                quota_auto=qs.auto.to_dict(),
                quota_fast=qs.fast.to_dict(),
                quota_expert=qs.expert.to_dict(),
                usage_use_delta=record.usage_use_count,
                usage_fail_delta=record.usage_fail_count,
                usage_sync_delta=record.usage_sync_count,
                last_use_at=record.last_use_at,
                last_fail_at=record.last_fail_at,
                last_fail_reason=record.last_fail_reason,
                last_sync_at=record.last_sync_at,
                last_clear_at=record.last_clear_at,
                state_reason=record.state_reason,
                ext_merge=record.ext,
            )
        ]
    )
    await repo.delete_accounts([old_token])

    logger.info(
        "admin token replaced: previous_token={} current_token={} pool={}",
        _mask(old_token),
        _mask(new_token),
        pool,
    )
    return _json({"status": "success", "token": new_token, "pool": pool})


@router.post("/tokens/disabled")
async def toggle_token_disabled(
    req: ToggleTokenDisabledRequest,
    repo: "AccountRepository" = Depends(get_repo),
):
    token = _sanitize(req.token)
    if not token:
        raise ValidationError("Token is required", param="token")

    records = await repo.get_accounts([token])
    if not records:
        raise AppError(
            "Account not found",
            kind=ErrorKind.VALIDATION,
            code="account_not_found",
            status=404,
        )
    record = records[0]

    if req.disabled:
        await repo.patch_accounts(
            [
                AccountPatch(
                    token=token,
                    status=AccountStatus.DISABLED,
                    state_reason="operator_disabled",
                    ext_merge={
                        **record.ext,
                        "disabled_at": now_ms(),
                        "disabled_reason": "operator_disabled",
                    },
                )
            ]
        )
        logger.info("admin token disabled: token={}", _mask(token))
        return _json({"status": "success", "token": token, "disabled": True})

    await repo.patch_accounts(
        [
            AccountPatch(
                token=token,
                status=AccountStatus.ACTIVE,
                clear_failures=True,
            )
        ]
    )
    logger.info("admin token restored: token={}", _mask(token))
    return _json({"status": "success", "token": token, "disabled": False})


@router.post("/tokens/disabled/batch")
async def toggle_tokens_disabled(
    req: ToggleTokensDisabledRequest,
    repo: "AccountRepository" = Depends(get_repo),
):
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in req.tokens:
        token = _sanitize(raw)
        if token and token not in seen:
            seen.add(token)
            cleaned.append(token)
    if not cleaned:
        raise ValidationError("No valid tokens provided", param="tokens")

    records = await repo.get_accounts(cleaned)
    if not records:
        raise AppError(
            "No matching accounts found",
            kind=ErrorKind.VALIDATION,
            code="account_not_found",
            status=404,
        )

    ts = now_ms()
    patches: list[AccountPatch] = []
    for record in records:
        if req.disabled:
            patches.append(
                AccountPatch(
                    token=record.token,
                    status=AccountStatus.DISABLED,
                    state_reason="operator_disabled",
                    ext_merge={
                        **record.ext,
                        "disabled_at": ts,
                        "disabled_reason": "operator_disabled",
                    },
                )
            )
        else:
            patches.append(
                AccountPatch(
                    token=record.token,
                    status=AccountStatus.ACTIVE,
                    clear_failures=True,
                )
            )

    result = await repo.patch_accounts(patches)
    logger.info(
        "admin tokens disabled batch updated: disabled={} requested_count={} patched_count={}",
        req.disabled,
        len(cleaned),
        result.patched,
    )
    return _json(
        {
            "status": "success",
            "disabled": req.disabled,
            "summary": {
                "total": len(cleaned),
                "ok": result.patched,
                "fail": max(0, len(cleaned) - result.patched),
            },
        }
    )


@router.put("/tokens/pool")
async def replace_pool(
    req: ReplacePoolRequest,
    auto_nsfw: bool = Query(False),
    repo: "AccountRepository" = Depends(get_repo),
    refresh_svc: "AccountRefreshService" = Depends(get_refresh_svc),
):
    cleaned = [t for t in (_sanitize(t) for t in req.tokens) if t]
    upserts = [
        AccountUpsert(token=t, pool=req.pool, tags=req.tags, provider=infer_provider(t))
        for t in cleaned
    ]
    await repo.replace_pool(BulkReplacePoolCommand(pool=req.pool, upserts=upserts))
    logger.info("admin pool replaced: pool={} token_count={}", req.pool, len(cleaned))
    if cleaned:
        _fire_and_forget(
            _refresh_then_auto_nsfw(
                refresh_svc,
                repo,
                cleaned,
                auto_nsfw_enabled=auto_nsfw,
            )
        )
    return _json({"pool": req.pool, "count": len(cleaned)})


# ---------------------------------------------------------------------------
# Build account management endpoints
# ---------------------------------------------------------------------------


class BuildPollRequest(BaseModel):
    device_code: str


class BuildConvertRequest(BaseModel):
    sso_tokens: list[str] = Field(default_factory=list)
    all: bool = False


class BuildBillingRefreshRequest(BaseModel):
    tokens: list[str] = []


@router.post("/tokens/build-start")
async def build_start():
    """Start OAuth Device Flow for Build account import.

    Returns user_code + verification_uri for the admin to open in browser.
    """
    from app.platform.auth.oauth_device import DeviceFlowClient

    client = DeviceFlowClient()
    try:
        resp = await client.start_device()
        return _json(
            {
                "status": "success",
                "device_code": resp.device_code,
                "user_code": resp.user_code,
                "verification_uri": resp.verification_uri,
                "verification_uri_complete": resp.verification_uri_complete,
                "interval": resp.interval,
                "expires_in": resp.expires_in,
            }
        )
    except Exception as exc:
        logger.warning("build-start failed: error={}", exc)
        raise AppError(
            "Failed to start Build device flow",
            kind=ErrorKind.UPSTREAM,
            code="build_device_flow_failed",
            status=502,
        ) from exc


@router.post("/tokens/build-poll")
async def build_poll(
    req: BuildPollRequest,
    repo: "AccountRepository" = Depends(get_repo),
):
    """Poll OAuth Device Flow completion and import Build account on success."""
    from app.platform.auth.oauth_device import (
        AccessDenied,
        AuthorizationPending,
        DeviceFlowClient,
        ExpiredToken,
        SlowDown,
    )
    from app.control.account.build_refresh import compute_refresh_due_at

    client = DeviceFlowClient()
    try:
        token_resp = await client.poll_device(req.device_code)
    except AuthorizationPending:
        return _json({"status": "pending"})
    except SlowDown:
        return _json({"status": "pending", "slow_down": True})
    except (AccessDenied, ExpiredToken):
        return _json({"status": "denied"})
    except Exception as exc:
        logger.warning("build-poll failed: error={}", exc)
        return _json({"status": "error", "message": str(exc)})

    access_token = token_resp.access_token
    now = now_ms()
    expires_at = now + token_resp.expires_in * 1000
    refresh_due_at = int(compute_refresh_due_at(expires_at / 1000, access_token) * 1000)

    ext_data = {
        "build_access_token": token_resp.access_token,
        "build_refresh_token": token_resp.refresh_token,
        "build_id_token": token_resp.id_token,
        "build_expires_at": int(expires_at),
        "build_refresh_due_at": refresh_due_at,
    }

    await repo.upsert_accounts(
        [
            AccountUpsert(
                token=access_token,
                pool="build",
                provider="grok_build",
                tags=["build"],
                ext=ext_data,
            )
        ]
    )

    logger.info("build account imported: token={}...", access_token[:10])
    return _json({"status": "success", "token": access_token, "pool": "build"})


@router.post("/tokens/build-convert")
async def build_convert(
    req: BuildConvertRequest,
    repo: "AccountRepository" = Depends(get_repo),
):
    """Batch-convert SSO tokens to Build OAuth credentials."""
    from app.control.account.sso_build import convert_sso_to_build, decode_build_claims
    from app.control.account.build_refresh import compute_refresh_due_at
    from app.control.account.invalid_credentials import (
        mark_account_reauth_required,
    )
    from app.dataplane.reverse.protocol.xai_billing import fetch_build_billing
    from app.platform.runtime.batch import run_batch

    results: dict = {"success": 0, "failed": 0, "errors": []}
    if req.all:
        tokens = []
        page_num = 1
        while True:
            page = await repo.list_accounts(
                ListAccountsQuery(page=page_num, page_size=2000)
            )
            tokens.extend(
                r.token for r in page.items if r.pool != "build" and not r.is_deleted()
            )
            if page_num * 2000 >= page.total:
                break
            page_num += 1
        if not tokens:
            return _json({**results, "message": "no convertible accounts"})
    else:
        tokens = req.sso_tokens

    async def _convert_one(sso_token: str) -> None:
        try:
            sso_token_clean = sso_token.strip()
            creds = await convert_sso_to_build(sso_token_clean)
            access_token = creds["access_token"]
            if not access_token or not access_token.strip():
                raise RuntimeError("minted Build access_token is empty")
            now = now_ms()

            # Real JWT exp wins over now + expires_in arithmetic
            claims = decode_build_claims(access_token)
            exp = claims.get("exp") if isinstance(claims, dict) else None
            if isinstance(exp, (int, float)):
                expires_at = int(exp) * 1000
                if expires_at <= now:
                    raise RuntimeError("minted Build access_token already expired")
            else:
                expires_at = now + int(creds.get("expires_in", "3600")) * 1000

            # Smoke verification: real token must pass an authenticated billing call
            billing = await fetch_build_billing(access_token)
            billing_info = {
                "plan_code": billing.plan_code,
                "plan_name": billing.plan_name,
                "monthly_limit": float(billing.monthly_limit),
                "used": float(billing.used),
                "remaining": float(billing.monthly_limit - billing.used),
                "on_demand_cap": float(billing.on_demand_cap),
                "on_demand_used": float(billing.on_demand_used),
                "prepaid_balance": float(billing.prepaid_balance),
                "synced_at": now,
            }
            refresh_due_at = int(
                compute_refresh_due_at(expires_at / 1000, access_token) * 1000
            )
            sso_hash = hashlib.sha256(sso_token_clean.encode()).hexdigest()[:16]

            ext_data = {
                "build_access_token": creds["access_token"],
                "build_refresh_token": creds.get("refresh_token", ""),
                "build_id_token": creds.get("id_token", ""),
                "build_expires_at": int(expires_at),
                "build_refresh_due_at": refresh_due_at,
                "converted_from_token": sso_hash,
                "converted_at": now,
                "build_billing": billing_info,
            }

            await repo.upsert_accounts(
                [
                    AccountUpsert(
                        token=access_token,
                        pool="build",
                        provider="grok_build",
                        tags=["build"],
                        ext=ext_data,
                    )
                ]
            )

            # Link back to the Web account that owns this SSO token
            sso_records = await repo.get_accounts([sso_token_clean])
            for rec in sso_records:
                if rec.pool != "build" and not rec.is_deleted():
                    existing_ext = rec.ext or {}
                    await repo.patch_accounts(
                        [
                            AccountPatch(
                                token=rec.token,
                                ext_merge={
                                    **existing_ext,
                                    "build_linked_at": now,
                                    "build_linked_token": access_token[:16],
                                },
                            )
                        ]
                    )
                    break

            results["success"] += 1
        except Exception as exc:
            results["failed"] += 1
            results["errors"].append(str(exc))
            logger.warning("SSO→Build conversion failed: error={}", exc)
            # Align Go markSSOCredentialRejected: rejected credentials (incl.
            # SSOCredentialRejected, which carries credential_rejected=True)
            # preserve the source SSO account as REAUTH_REQUIRED — the SSO
            # cookie may still work on Web/Console even when Build minting
            # rejects it. Only body-marker-confirmed deaths become EXPIRED.
            if isinstance(exc, UpstreamError) and exc.credential_rejected:
                try:
                    await mark_account_reauth_required(
                        repo,
                        sso_token_clean,
                        str(exc) or "sso credential rejected",
                        source="sso→build convert",
                    )
                except Exception:
                    logger.warning(
                        "failed to mark SSO credential rejected: token={}...",
                        sso_token_clean[:10],
                    )

    await run_batch(
        [t for t in tokens if t.strip()],
        _convert_one,
        concurrency=5,
    )

    logger.info(
        "batch SSO→Build conversion completed: success={} failed={}",
        results["success"],
        results["failed"],
    )
    return _json(results)


@router.post("/tokens/build/refresh-billing")
async def build_refresh_billing(
    req: BuildBillingRefreshRequest,
    repo: "AccountRepository" = Depends(get_repo),
):
    """Refresh Build billing data for specified accounts.

    Queries upstream billing API for each Build account and updates
    the billing info in ext dict.
    """
    if req.tokens:
        records = await repo.get_accounts(req.tokens)
    else:
        page = await repo.list_accounts(ListAccountsQuery(page=1, page_size=5000))
        records = [r for r in page.items if r.pool == "build" and not r.is_deleted()]

    results: dict = {"refreshed": 0, "failed": 0, "errors": []}

    from app.dataplane.reverse.protocol.xai_billing import fetch_build_billing

    async def _refresh_one(record) -> None:
        try:
            ext = record.ext or {}
            access_token = ext.get("build_access_token", record.token)
            billing = await fetch_build_billing(access_token)

            billing_info = {
                "plan_code": billing.plan_code,
                "plan_name": billing.plan_name,
                "monthly_limit": float(billing.monthly_limit),
                "used": float(billing.used),
                "remaining": float(billing.monthly_limit - billing.used),
                "on_demand_cap": float(billing.on_demand_cap),
                "on_demand_used": float(billing.on_demand_used),
                "prepaid_balance": float(billing.prepaid_balance),
                "synced_at": now_ms(),
            }

            await repo.patch_accounts(
                [
                    AccountPatch(
                        token=record.token,
                        ext_merge={**ext, "build_billing": billing_info},
                    )
                ]
            )
            results["refreshed"] += 1

        except Exception as exc:
            results["failed"] += 1
            results["errors"].append(str(exc))
            logger.warning(
                "build billing refresh failed: token={}... error={}",
                record.token[:10],
                exc,
            )

    from app.platform.runtime.batch import run_batch

    await run_batch(records, _refresh_one, concurrency=5)

    logger.info(
        "build billing refresh completed: refreshed={} failed={}",
        results["refreshed"],
        results["failed"],
    )
    return _json(results)


# ---------------------------------------------------------------------------
# Fire-and-forget import refresh
# ---------------------------------------------------------------------------


async def _refresh_imported(svc: "AccountRefreshService", tokens: list[str]) -> bool:
    try:
        await svc.refresh_on_import(tokens)
        logger.info("admin import quota sync completed: token_count={}", len(tokens))
        return True
    except Exception as exc:
        logger.warning(
            "admin import quota sync failed: token_count={} error={}", len(tokens), exc
        )
        return False


async def _refresh_then_auto_nsfw(
    svc: "AccountRefreshService",
    repo: "AccountRepository",
    tokens: list[str],
    *,
    auto_nsfw_enabled: bool,
) -> None:
    unique_tokens = list(dict.fromkeys(tokens))
    if await _refresh_imported(svc, unique_tokens):
        _schedule_auto_nsfw(repo, unique_tokens, enabled=auto_nsfw_enabled)


async def _enable_nsfw_imported(repo: "AccountRepository", tokens: list[str]) -> None:
    from app.products.web.admin.batch import _concurrency, _nsfw_one
    from app.platform.runtime.batch import run_batch

    records = await repo.get_accounts(tokens)
    by_token = {r.token: r for r in records}
    manageable_tokens = [
        token
        for token in tokens
        if (record := by_token.get(token)) and is_manageable(record)
    ]
    skipped_c = len(tokens) - len(manageable_tokens)
    if not manageable_tokens:
        logger.info(
            "admin import auto nsfw skipped: token_count={} skipped_non_manageable={}",
            len(tokens),
            skipped_c,
        )
        return

    ok_c = fail_c = 0

    async def _one(token: str) -> None:
        nonlocal ok_c, fail_c
        try:
            await _nsfw_one(repo, token, True)
            ok_c += 1
        except Exception as exc:
            fail_c += 1
            logger.warning(
                "admin import auto nsfw failed: token={} error={}", _mask(token), exc
            )

    await run_batch(
        manageable_tokens,
        _one,
        concurrency=_concurrency(None, "batch.nsfw_concurrency"),
    )
    logger.info(
        "admin import auto nsfw completed: token_count={} skipped_non_manageable={} ok={} failed={}",
        len(manageable_tokens),
        skipped_c,
        ok_c,
        fail_c,
    )
