"""SQLite client-key repository (WAL mode, mirrors LocalAccountRepository)."""

import asyncio
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.platform.runtime.clock import now_ms

from .model import ClientKey

_TBL = "client_keys"


class ClientKeyRepository:
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
                    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                    name                   TEXT    NOT NULL,
                    prefix                 TEXT    NOT NULL UNIQUE,
                    secret                 TEXT    NOT NULL,
                    enabled                INTEGER NOT NULL DEFAULT 1,
                    expires_at             INTEGER,
                    rpm_limit              INTEGER NOT NULL DEFAULT 120,
                    max_concurrent         INTEGER NOT NULL DEFAULT 8,
                    billing_limit_usd_ticks INTEGER NOT NULL DEFAULT 0,
                    billed_usage_usd_ticks INTEGER NOT NULL DEFAULT 0,
                    allow_model_aliases    INTEGER NOT NULL DEFAULT 0,
                    allowed_model_ids      TEXT    NOT NULL DEFAULT '[]',
                    provider_scope         TEXT    NOT NULL DEFAULT '[]',
                    tier_scope             TEXT    NOT NULL DEFAULT '[]',
                    last_used_at           INTEGER,
                    created_at             INTEGER NOT NULL,
                    updated_at             INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ck_prefix ON {_TBL} (prefix);
                CREATE INDEX IF NOT EXISTS idx_ck_enabled ON {_TBL} (enabled);
                """
            )
            conn.commit()

    @staticmethod
    def _row_to_key(row: sqlite3.Row) -> ClientKey:
        d = dict(row)
        d["enabled"] = bool(d["enabled"])
        d["allow_model_aliases"] = bool(d["allow_model_aliases"])
        d["allowed_model_ids"] = json.loads(d["allowed_model_ids"] or "[]")
        d["provider_scope"] = json.loads(d["provider_scope"] or "[]")
        d["tier_scope"] = json.loads(d["tier_scope"] or "[]")
        return ClientKey(**d)

    @staticmethod
    def _key_to_row(key: ClientKey) -> dict[str, Any]:
        d = asdict(key)
        d["enabled"] = int(d["enabled"])
        d["allow_model_aliases"] = int(d["allow_model_aliases"])
        d["allowed_model_ids"] = json.dumps(d["allowed_model_ids"])
        d["provider_scope"] = json.dumps(d["provider_scope"])
        d["tier_scope"] = json.dumps(d["tier_scope"])
        return d

    @staticmethod
    def _normalize_page(page: int, page_size: int) -> tuple[int, int]:
        return max(1, page), min(max(1, page_size), 200)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._init_sync)

    async def create(self, key: ClientKey) -> ClientKey:
        def _sync() -> ClientKey:
            ts = now_ms()
            row = self._key_to_row(key)
            with closing(self._connect()) as conn:
                cursor = conn.execute(
                    f"""
                    INSERT INTO {_TBL} (
                        name, prefix, secret, enabled, expires_at,
                        rpm_limit, max_concurrent, billing_limit_usd_ticks,
                        billed_usage_usd_ticks, allow_model_aliases,
                        allowed_model_ids, provider_scope, tier_scope,
                        last_used_at, created_at, updated_at
                    ) VALUES (
                        :name, :prefix, :secret, :enabled, :expires_at,
                        :rpm_limit, :max_concurrent, :billing_limit_usd_ticks,
                        :billed_usage_usd_ticks, :allow_model_aliases,
                        :allowed_model_ids, :provider_scope, :tier_scope,
                        :last_used_at, :created_at, :updated_at
                    )
                    """,
                    {
                        **row,
                        "created_at": ts,
                        "updated_at": ts,
                        "last_used_at": None,
                    },
                )
                if cursor.lastrowid is not None:
                    key.id = int(cursor.lastrowid)
                key.created_at = ts
                key.updated_at = ts
                key.last_used_at = None
                conn.commit()
                return key

        async with self._lock:
            return await asyncio.to_thread(_sync)

    async def get(self, key_id: int) -> ClientKey | None:
        def _sync() -> ClientKey | None:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    f"SELECT * FROM {_TBL} WHERE id = ?", (key_id,)
                ).fetchone()
                return self._row_to_key(row) if row else None

        return await asyncio.to_thread(_sync)

    async def get_by_prefix(self, prefix: str) -> ClientKey | None:
        def _sync() -> ClientKey | None:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    f"SELECT * FROM {_TBL} WHERE prefix = ?", (prefix,)
                ).fetchone()
                return self._row_to_key(row) if row else None

        return await asyncio.to_thread(_sync)

    async def list_keys(
        self,
        page: int = 1,
        page_size: int = 20,
        search: str = "",
        status: str = "",
        sort_by: str = "updated_at",
        sort_desc: bool = True,
    ) -> tuple[list[ClientKey], int]:
        def _sync() -> tuple[list[ClientKey], int]:
            p, ps = self._normalize_page(page, page_size)
            where: list[str] = []
            params: list[Any] = []
            if search:
                where.append("(name LIKE ? OR prefix LIKE ?)")
                like = f"%{search}%"
                params.extend([like, like])
            if status == "enabled":
                where.append("enabled = 1")
            elif status == "disabled":
                where.append("enabled = 0")
            where_sql = ("WHERE " + " AND ".join(where)) if where else ""
            safe_sort = (
                sort_by
                if sort_by in {"id", "name", "created_at", "updated_at", "last_used_at"}
                else "updated_at"
            )
            order = "DESC" if sort_desc else "ASC"
            with closing(self._connect()) as conn:
                total = conn.execute(
                    f"SELECT COUNT(*) FROM {_TBL} {where_sql}", params
                ).fetchone()[0]
                rows = conn.execute(
                    f"SELECT * FROM {_TBL} {where_sql} "
                    f"ORDER BY {safe_sort} {order} LIMIT ? OFFSET ?",
                    params + [ps, (p - 1) * ps],
                ).fetchall()
                return [self._row_to_key(r) for r in rows], int(total)

        return await asyncio.to_thread(_sync)

    async def update(self, key: ClientKey) -> ClientKey:
        def _sync() -> ClientKey:
            row = self._key_to_row(key)
            with closing(self._connect()) as conn:
                conn.execute(
                    f"""
                    UPDATE {_TBL} SET
                        name = :name, enabled = :enabled, expires_at = :expires_at,
                        rpm_limit = :rpm_limit, max_concurrent = :max_concurrent,
                        billing_limit_usd_ticks = :billing_limit_usd_ticks,
                        allow_model_aliases = :allow_model_aliases,
                        allowed_model_ids = :allowed_model_ids,
                        provider_scope = :provider_scope, tier_scope = :tier_scope,
                        updated_at = :updated_at
                    WHERE id = :id
                    """,
                    {**row, "updated_at": now_ms()},
                )
                conn.commit()
                return key

        async with self._lock:
            return await asyncio.to_thread(_sync)

    async def batch_set_enabled(self, ids: list[int], enabled: bool) -> int:
        def _sync() -> int:
            with closing(self._connect()) as conn:
                cursor = conn.executemany(
                    f"UPDATE {_TBL} SET enabled = ?, updated_at = ? WHERE id = ?",
                    [(int(enabled), now_ms(), i) for i in ids],
                )
                conn.commit()
                return cursor.rowcount if cursor.rowcount >= 0 else len(ids)

        async with self._lock:
            return await asyncio.to_thread(_sync)

    async def batch_delete(self, ids: list[int]) -> int:
        def _sync() -> int:
            with closing(self._connect()) as conn:
                cursor = conn.executemany(
                    f"DELETE FROM {_TBL} WHERE id = ?", [(i,) for i in ids]
                )
                conn.commit()
                return cursor.rowcount if cursor.rowcount >= 0 else len(ids)

        async with self._lock:
            return await asyncio.to_thread(_sync)

    async def delete(self, key_id: int) -> bool:
        def _sync() -> bool:
            with closing(self._connect()) as conn:
                conn.execute(f"DELETE FROM {_TBL} WHERE id = ?", (key_id,))
                conn.commit()
                return conn.execute("SELECT changes()").fetchone()[0] > 0

        async with self._lock:
            return await asyncio.to_thread(_sync)

    async def touch_usage(self, key_id: int, billed_ticks: int = 0) -> None:
        """Update last_used_at and accumulate billed usage (auth + audit path)."""

        def _sync() -> None:
            with closing(self._connect()) as conn:
                conn.execute(
                    f"UPDATE {_TBL} SET last_used_at = ?, "
                    f"billed_usage_usd_ticks = billed_usage_usd_ticks + ? "
                    f"WHERE id = ?",
                    (now_ms(), max(0, billed_ticks), key_id),
                )
                conn.commit()

        async with self._lock:
            await asyncio.to_thread(_sync)

    async def count(self, enabled_only: bool = False) -> int:
        def _sync() -> int:
            with closing(self._connect()) as conn:
                if enabled_only:
                    row = conn.execute(
                        f"SELECT COUNT(*) FROM {_TBL} WHERE enabled = 1"
                    ).fetchone()
                else:
                    row = conn.execute(f"SELECT COUNT(*) FROM {_TBL}").fetchone()
                return int(row[0])

        return await asyncio.to_thread(_sync)

    async def close(self) -> None:
        """No-op — connections are opened and closed per operation."""


__all__ = ["ClientKeyRepository"]
