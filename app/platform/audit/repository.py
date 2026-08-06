"""SQLite append-only audit repository (WAL mode).

Records are written immediately (SQLite single-insert is sub-ms at this
volume); a buffered flush adds complexity for no measured gain.
"""

import asyncio
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.platform.runtime.clock import now_ms

from .model import AuditAttempt, AuditRecord

_TBL = "audits"
_ATT = "audit_attempts"

# period → (label, start_delta_ms).  Windows are anchored to now (Go anchors
# to hour/day boundaries; now-anchoring is equivalent for aggregation).
_PERIODS = {
    "24h": 24 * 3600_000,
    "7d": 7 * 24 * 3600_000,
    "30d": 30 * 24 * 3600_000,
    "90d": 90 * 24 * 3600_000,
}


class AuditRepository:
    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError:
            pass
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS {_TBL} (
                    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id               TEXT    NOT NULL,
                    client_key_id            INTEGER,
                    client_key_name          TEXT    NOT NULL DEFAULT '',
                    model                    TEXT    NOT NULL DEFAULT '',
                    provider                 TEXT    NOT NULL DEFAULT '',
                    operation                TEXT    NOT NULL DEFAULT '',
                    status_code              INTEGER NOT NULL DEFAULT 200,
                    streaming                INTEGER NOT NULL DEFAULT 0,
                    input_tokens             INTEGER NOT NULL DEFAULT 0,
                    output_tokens            INTEGER NOT NULL DEFAULT 0,
                    reasoning_tokens         INTEGER NOT NULL DEFAULT 0,
                    total_tokens             INTEGER NOT NULL DEFAULT 0,
                    cost_in_usd_ticks        INTEGER NOT NULL DEFAULT 0,
                    estimated_cost_in_usd_ticks INTEGER NOT NULL DEFAULT 0,
                    first_token_ms           INTEGER,
                    duration_ms              INTEGER NOT NULL DEFAULT 0,
                    error_code               TEXT    NOT NULL DEFAULT '',
                    attempt_count            INTEGER NOT NULL DEFAULT 1,
                    created_at               INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS {_ATT} (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    audit_id            INTEGER NOT NULL REFERENCES {_TBL}(id) ON DELETE CASCADE,
                    number              INTEGER NOT NULL DEFAULT 1,
                    source              TEXT    NOT NULL DEFAULT 'upstream_http',
                    stage               TEXT    NOT NULL DEFAULT 'gateway',
                    method              TEXT    NOT NULL DEFAULT '',
                    request_path        TEXT    NOT NULL DEFAULT '',
                    upstream_url        TEXT    NOT NULL DEFAULT '',
                    started_at          INTEGER NOT NULL DEFAULT 0,
                    duration_ms         INTEGER NOT NULL DEFAULT 0,
                    upstream_status_code INTEGER,
                    upstream_status     TEXT    NOT NULL DEFAULT '',
                    transport_error     TEXT    NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_audit_created ON {_TBL} (created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_audit_key ON {_TBL} (client_key_id);
                CREATE INDEX IF NOT EXISTS idx_audit_model ON {_TBL} (model);
                CREATE INDEX IF NOT EXISTS idx_attempt_audit ON {_ATT} (audit_id);
                """
            )
            conn.commit()

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> AuditRecord:
        d = dict(row)
        d["streaming"] = bool(d["streaming"])
        return AuditRecord(**d)

    @staticmethod
    def _row_to_attempt(row: sqlite3.Row) -> AuditAttempt:
        d = dict(row)
        d.pop("id", None)
        d.pop("audit_id", None)
        return AuditAttempt(**d)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._init_sync)

    async def record(self, record: AuditRecord) -> int:
        def _sync() -> int:
            if not record.request_id:
                record.request_id = f"req_{now_ms()}_{id(record)}"
            if record.created_at == 0:
                record.created_at = now_ms()
            with closing(self._connect()) as conn:
                cursor = conn.execute(
                    f"""
                    INSERT INTO {_TBL} (
                        request_id, client_key_id, client_key_name, model, provider,
                        operation, status_code, streaming, input_tokens, output_tokens,
                        reasoning_tokens, total_tokens, cost_in_usd_ticks,
                        estimated_cost_in_usd_ticks, first_token_ms, duration_ms,
                        error_code, attempt_count, created_at
                    ) VALUES (
                        :request_id, :client_key_id, :client_key_name, :model, :provider,
                        :operation, :status_code, :streaming, :input_tokens, :output_tokens,
                        :reasoning_tokens, :total_tokens, :cost_in_usd_ticks,
                        :estimated_cost_in_usd_ticks, :first_token_ms, :duration_ms,
                        :error_code, :attempt_count, :created_at
                    )
                    """,
                    asdict(record),
                )
                audit_id = int(cursor.lastrowid or 0)
                if audit_id and record.attempts:
                    conn.executemany(
                        f"""
                        INSERT INTO {_ATT} (
                            audit_id, number, source, stage, method, request_path,
                            upstream_url, started_at, duration_ms,
                            upstream_status_code, upstream_status, transport_error
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                audit_id,
                                a.number,
                                a.source,
                                a.stage,
                                a.method,
                                a.request_path,
                                a.upstream_url,
                                a.started_at,
                                a.duration_ms,
                                a.upstream_status_code,
                                a.upstream_status,
                                a.transport_error,
                            )
                            for a in record.attempts
                        ],
                    )
                conn.commit()
                return audit_id

        async with self._lock:
            return await asyncio.to_thread(_sync)

    async def get(self, audit_id: int) -> AuditRecord | None:
        def _sync() -> AuditRecord | None:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    f"SELECT * FROM {_TBL} WHERE id = ?", (audit_id,)
                ).fetchone()
                if row is None:
                    return None
                record = self._row_to_record(row)
                attempts = conn.execute(
                    f"SELECT * FROM {_ATT} WHERE audit_id = ? ORDER BY number",
                    (audit_id,),
                ).fetchall()
                record.attempts = [self._row_to_attempt(r) for r in attempts]
                return record

        return await asyncio.to_thread(_sync)

    @staticmethod
    def _filters(
        search: str = "",
        model: str = "",
        status: str = "",
        key: str = "",
        account: str = "",
    ) -> tuple[str, list[Any]]:
        where: list[str] = []
        params: list[Any] = []
        if search:
            where.append(
                "(request_id LIKE ? OR client_key_name LIKE ? OR model LIKE ?)"
            )
            like = f"%{search}%"
            params.extend([like, like, like])
        if model:
            where.append("model = ?")
            params.append(model)
        if status == "success":
            where.append("status_code >= 200 AND status_code < 300")
        elif status == "failed":
            where.append("NOT (status_code >= 200 AND status_code < 300)")
        if key:
            where.append("client_key_id = ?")
            params.append(int(key) if str(key).isdigit() else -1)
        if account:
            where.append("client_key_name LIKE ?")
            params.append(f"%{account}%")
        return ("WHERE " + " AND ".join(where)) if where else "", params

    async def list_records(
        self,
        page: int = 1,
        page_size: int = 20,
        search: str = "",
        model: str = "",
        status: str = "",
        key: str = "",
        account: str = "",
    ) -> tuple[list[AuditRecord], int]:
        def _sync() -> tuple[list[AuditRecord], int]:
            p, ps = max(1, page), min(max(1, page_size), 200)
            where_sql, params = self._filters(search, model, status, key, account)
            with closing(self._connect()) as conn:
                total = conn.execute(
                    f"SELECT COUNT(*) FROM {_TBL} {where_sql}", params
                ).fetchone()[0]
                rows = conn.execute(
                    f"SELECT * FROM {_TBL} {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
                    params + [ps, (p - 1) * ps],
                ).fetchall()
                return [self._row_to_record(r) for r in rows], int(total)

        return await asyncio.to_thread(_sync)

    async def list_cursor(
        self,
        cursor: str = "",
        page_size: int = 50,
        search: str = "",
        period: str = "24h",
        model: str = "",
        status: str = "",
        key: str = "",
        account: str = "",
    ) -> tuple[list[AuditRecord], str, bool]:
        """Keyset pagination on id (cursor = last seen audit id)."""

        def _sync() -> tuple[list[AuditRecord], str, bool]:
            ps = min(max(1, page_size), 200)
            since = int(cursor) if str(cursor).isdigit() else 2**63 - 1
            delta_ms = _PERIODS.get(period)
            if delta_ms is None:
                raise ValueError(f"invalid period: {period}")
            where_sql, params = self._filters(search, model, status, key, account)
            where_sql = (
                f"{where_sql} AND id < ? AND created_at >= ?"
                if where_sql
                else "WHERE id < ? AND created_at >= ?"
            )
            params += [since, now_ms() - delta_ms]
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    f"SELECT * FROM {_TBL} {where_sql} ORDER BY id DESC LIMIT ?",
                    params + [ps + 1],
                ).fetchall()
                has_more = len(rows) > ps
                rows = rows[:ps]
                next_cursor = str(rows[-1]["id"]) if rows and has_more else ""
                return [self._row_to_record(r) for r in rows], next_cursor, has_more

        return await asyncio.to_thread(_sync)

    async def summary(
        self,
        period: str = "24h",
        search: str = "",
        model: str = "",
        status: str = "",
        key: str = "",
        account: str = "",
    ) -> dict[str, Any]:
        """Aggregate usage over the window (port of Go audit Summary)."""

        def _sync() -> dict[str, Any]:
            delta_ms = _PERIODS.get(period)
            if delta_ms is None:
                raise ValueError(f"invalid period: {period}")
            where_sql, params = self._filters(search, model, status, key, account)
            where_sql = (
                f"{where_sql} AND created_at >= ?"
                if where_sql
                else "WHERE created_at >= ?"
            )
            params.append(now_ms() - delta_ms)
            with closing(self._connect()) as conn:
                row = conn.execute(
                    f"""
                    SELECT
                        COUNT(*)                                   AS requests,
                        SUM(CASE WHEN status_code >= 200 AND status_code < 300 THEN 1 ELSE 0 END) AS successful,
                        SUM(input_tokens)                         AS input_tokens,
                        SUM(output_tokens)                        AS output_tokens,
                        SUM(reasoning_tokens)                     AS reasoning_tokens,
                        SUM(total_tokens)                         AS total_tokens,
                        SUM(duration_ms)                          AS duration_ms,
                        SUM(estimated_cost_in_usd_ticks)          AS cost_ticks
                    FROM {_TBL} {where_sql}
                    """,
                    params,
                ).fetchone()
                requests = int(row["requests"] or 0)
                successful = int(row["successful"] or 0)
                duration_ms = int(row["duration_ms"] or 0)
                return {
                    "requests": requests,
                    "successfulRequests": successful,
                    "failedRequests": requests - successful,
                    "inputTokens": int(row["input_tokens"] or 0),
                    "outputTokens": int(row["output_tokens"] or 0),
                    "reasoningTokens": int(row["reasoning_tokens"] or 0),
                    "totalTokens": int(row["total_tokens"] or 0),
                    "averageDurationMs": round(duration_ms / requests, 2)
                    if requests
                    else 0.0,
                    "successRate": round(successful / requests * 100, 2)
                    if requests
                    else 0.0,
                    "estimatedCostInUsdTicks": int(row["cost_ticks"] or 0),
                }

        return await asyncio.to_thread(_sync)

    async def dashboard_aggregate(self, period: str = "24h") -> dict[str, Any]:
        """Bucket-level aggregation for the dashboard endpoint."""

        def _sync() -> dict[str, Any]:
            delta_ms = _PERIODS.get(period)
            if delta_ms is None:
                raise ValueError(f"invalid period: {period}")
            bucket_ms = 3600_000 if period == "24h" else 24 * 3600_000
            if period == "90d":
                bucket_ms = 7 * 24 * 3600_000
            start = now_ms() - delta_ms
            with closing(self._connect()) as conn:
                series_rows = conn.execute(
                    f"""
                    SELECT
                        (created_at - ?) / ? * ? + ? AS bucket_start,
                        COUNT(*)                    AS requests,
                        SUM(input_tokens)           AS input_tokens,
                        SUM(output_tokens)          AS output_tokens,
                        SUM(reasoning_tokens)       AS reasoning_tokens,
                        SUM(total_tokens)           AS total_tokens,
                        SUM(estimated_cost_in_usd_ticks) AS cost_ticks
                    FROM {_TBL} WHERE created_at >= ?
                    GROUP BY bucket_start ORDER BY bucket_start
                    """,
                    (start, bucket_ms, bucket_ms, start, start),
                ).fetchall()
                models_rows = conn.execute(
                    f"""
                    SELECT model AS model, COUNT(*) AS requests, SUM(total_tokens) AS tokens
                    FROM {_TBL} WHERE created_at >= ?
                    GROUP BY model ORDER BY requests DESC LIMIT 10
                    """,
                    (start,),
                ).fetchall()
                usage_row = conn.execute(
                    f"""
                    SELECT
                        COUNT(*) AS requests,
                        SUM(CASE WHEN status_code >= 200 AND status_code < 300 THEN 1 ELSE 0 END) AS successful,
                        SUM(input_tokens) AS input_tokens,
                        SUM(output_tokens) AS output_tokens,
                        SUM(reasoning_tokens) AS reasoning_tokens,
                        SUM(total_tokens) AS total_tokens,
                        SUM(estimated_cost_in_usd_ticks) AS cost_ticks,
                        SUM(duration_ms) AS duration_ms
                    FROM {_TBL} WHERE created_at >= ?
                    """,
                    (start,),
                ).fetchone()
                return {
                    "series": [dict(r) for r in series_rows],
                    "topModels": [dict(r) for r in models_rows],
                    "usage": dict(usage_row),
                }

        return await asyncio.to_thread(_sync)

    async def close(self) -> None:
        """No-op — connections are opened and closed per operation."""


__all__ = ["AuditRepository"]
