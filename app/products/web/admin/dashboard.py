"""Dashboard aggregation endpoint (port of Go dashboard handler).

Aggregates request metrics from the audit table and resource counts from the
account directory + client key store.  ``refresh=1`` is a no-op — the Python
port computes live (no short cache) so every call is already fresh.
"""

from datetime import datetime, timezone as _tz
from typing import Any

import orjson
from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.platform.errors import AppError, ErrorKind, ValidationError

router = APIRouter(tags=["Admin - Dashboard"])

_TAG = "Admin - Dashboard"
_PERIODS = {"24h": "24h", "7d": "7d", "30d": "30d", "90d": "90d"}
USD_TICKS_PER_DOLLAR = 1_000_000


def _iso(ms: int) -> str:
    return (
        datetime.fromtimestamp(ms / 1000, tz=_tz.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


@router.get("/dashboard", tags=[_TAG])
async def dashboard(
    request: Request,
    period: str = Query("24h"),
    timezone: str = Query(""),
    refresh: int = Query(0),
):
    period = _PERIODS.get(period, "")
    if not period:
        raise ValidationError("period 仅支持 24h、7d、30d、90d", param="period")
    try:
        tz = ZoneInfo(timezone.strip() or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        raise ValidationError("timezone 必须是有效的 IANA 时区", param="timezone")

    audit_repo = getattr(request.app.state, "audit_repo", None)
    if audit_repo is None:
        raise AppError(
            "Audit store not initialised",
            kind=ErrorKind.SERVER,
            code="audit_not_initialised",
            status=503,
        )
    agg = await audit_repo.dashboard_aggregate(period)

    usage_row = agg["usage"]
    requests = int(usage_row["requests"] or 0)
    successful = int(usage_row["successful"] or 0)
    tokens = int(usage_row["total_tokens"] or 0)
    cost_ticks = int(usage_row["cost_ticks"] or 0)
    duration_ms = int(usage_row["duration_ms"] or 0)

    series = []
    for point in agg["series"]:
        bucket_ms = int(point["bucket_start"])
        cost = int(point["cost_ticks"] or 0)
        series.append(
            {
                "bucket": _iso(bucket_ms),
                "requests": int(point["requests"] or 0),
                "tokens": int(point["total_tokens"] or 0),
                "costUsd": round(cost / USD_TICKS_PER_DOLLAR, 6),
            }
        )

    top_models = [
        {
            "model": m["model"] or "(unknown)",
            "requests": int(m["requests"] or 0),
            "tokens": int(m["tokens"] or 0),
        }
        for m in agg["topModels"]
    ]

    providers = await _provider_stats(request)

    payload = {
        "period": period,
        "timezone": str(tz),
        "generatedAt": _iso(int(datetime.now(_tz.utc).timestamp() * 1000)),
        "usage": {
            "totalRequests": requests,
            "successfulRequests": successful,
            "failedRequests": requests - successful,
            "successRate": round(successful / requests * 100, 2) if requests else 0.0,
            "totalTokens": tokens,
            "totalCostUsd": round(cost_ticks / USD_TICKS_PER_DOLLAR, 6),
            "averageFirstTokenMs": 0.0,  # first-token timing not captured in port
            "outputTokensPerSecond": 0.0,
        },
        "series": series,
        "topModels": top_models,
        "providers": providers,
    }
    return Response(content=orjson.dumps(payload), media_type="application/json")


async def _provider_stats(request: Request) -> list[dict[str, Any]]:
    """Account counts per provider from the directory's runtime snapshot."""
    snapshot = None
    directory = getattr(request.app.state, "directory", None)
    if directory is not None:
        try:
            snapshot = await directory.repository.runtime_snapshot()
        except Exception:
            snapshot = None
    if snapshot is None:
        return []

    from app.control.account.enums import AccountStatus
    from app.control.account.state_machine import derive_status

    counts: dict[str, dict[str, int]] = {}
    for record in snapshot.items:
        provider = record.provider or "grok_web"
        entry = counts.setdefault(provider, {"accounts": 0, "available": 0})
        entry["accounts"] += 1
        if derive_status(record) == AccountStatus.ACTIVE:
            entry["available"] += 1
    return [
        {"provider": provider, **stats} for provider, stats in sorted(counts.items())
    ]


__all__ = ["router"]
