"""Request audit endpoints (port of Go audit handler).

Supports both page-based and cursor-based listing (Go ``pagination=cursor``),
window summary aggregation, and per-record detail with attempts.
"""

from datetime import datetime, timezone
from typing import Any

import orjson
from fastapi import APIRouter, Request, Path, Query
from fastapi.responses import Response

from app.platform.errors import AppError, ErrorKind, ValidationError

router = APIRouter(tags=["Admin - Request Audits"])

_TAG = "Admin - Request Audits"


def _audit_repo(request) -> Any:
    repo = getattr(request.app.state, "audit_repo", None)
    if repo is None:
        raise AppError(
            "Audit store not initialised",
            kind=ErrorKind.SERVER,
            code="audit_not_initialised",
            status=503,
        )
    return repo


def _iso(ms: int) -> str:
    return (
        datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _attempt_dto(a) -> dict[str, Any]:
    return {
        "number": a.number,
        "source": a.source,
        "stage": a.stage,
        "method": a.method,
        "requestPath": a.request_path,
        "upstreamUrl": a.upstream_url,
        "startedAt": _iso(a.started_at),
        "durationMs": a.duration_ms,
        "upstreamStatusCode": a.upstream_status_code,
        "upstreamStatus": a.upstream_status,
        "transportError": a.transport_error,
    }


def _record_dto(r) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "requestId": r.request_id,
        "clientKeyId": str(r.client_key_id) if r.client_key_id else None,
        "clientKeyName": r.client_key_name,
        "model": r.model,
        "provider": r.provider,
        "operation": r.operation,
        "statusCode": r.status_code,
        "streaming": r.streaming,
        "inputTokens": r.input_tokens,
        "outputTokens": r.output_tokens,
        "reasoningTokens": r.reasoning_tokens,
        "totalTokens": r.total_tokens,
        "costInUsdTicks": r.cost_in_usd_ticks,
        "estimatedCostInUsdTicks": r.estimated_cost_in_usd_ticks,
        "firstTokenMs": r.first_token_ms,
        "durationMs": r.duration_ms,
        "errorCode": r.error_code,
        "attemptCount": r.attempt_count,
        "createdAt": _iso(r.created_at),
        "attempts": [_attempt_dto(a) for a in r.attempts],
    }


def _list_filters(request) -> dict[str, str]:
    return {
        "search": request.query_params.get("search", ""),
        "model": request.query_params.get("model", ""),
        "status": request.query_params.get("status", ""),
        "key": request.query_params.get("key", ""),
        "account": request.query_params.get("account", ""),
    }


@router.get("/request-audits", tags=[_TAG])
async def list_request_audits(request: Request):
    repo = _audit_repo(request)
    filters = _list_filters(request)
    if request.query_params.get("pagination") == "cursor":
        try:
            items, next_cursor, has_more = await repo.list_cursor(
                cursor=request.query_params.get("cursor", ""),
                page_size=int(request.query_params.get("pageSize", "50")),
                period=request.query_params.get("period", "24h"),
                **filters,
            )
        except ValueError as exc:
            raise ValidationError(str(exc), param="period")
        return Response(
            content=orjson.dumps(
                {
                    "items": [_record_dto(r) for r in items],
                    "pageSize": len(items),
                    "nextCursor": next_cursor,
                    "hasMore": has_more,
                }
            ),
            media_type="application/json",
        )
    page = int(request.query_params.get("page", "1"))
    page_size = int(request.query_params.get("pageSize", "20"))
    items, total = await repo.list_records(page=page, page_size=page_size, **filters)
    return Response(
        content=orjson.dumps(
            {
                "items": [_record_dto(r) for r in items],
                "page": page,
                "pageSize": page_size,
                "total": total,
            }
        ),
        media_type="application/json",
    )


@router.get("/request-audits/summary", tags=[_TAG])
async def request_audits_summary(request: Request):
    repo = _audit_repo(request)
    try:
        usage = await repo.summary(
            period=request.query_params.get("period", "24h"),
            **_list_filters(request),
        )
    except ValueError as exc:
        raise ValidationError(str(exc), param="period")
    return {
        "period": request.query_params.get("period", "24h"),
        "generatedAt": _iso(int(datetime.now(timezone.utc).timestamp() * 1000)),
        "usage": usage,
    }


@router.get("/request-audits/{audit_id}", tags=[_TAG])
async def get_request_audit(request: Request, audit_id: int = Path(...)):
    repo = _audit_repo(request)
    record = await repo.get(audit_id)
    if record is None:
        raise AppError(
            "审计记录不存在", kind=ErrorKind.SERVER, code="auditNotFound", status=404
        )
    return {"audit": _record_dto(record), "attempts": record.attempts}


__all__ = ["router"]
