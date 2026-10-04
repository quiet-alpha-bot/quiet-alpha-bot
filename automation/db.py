"""PostgreSQL access (asyncpg): pool, migrations, leader lock, event ledger."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import asyncpg

from . import queries as q

logger = logging.getLogger("quiet-alpha.db")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (set, tuple)):
        return list(value)
    return str(value)


def to_json(value: Any) -> str:
    return json.dumps(value, default=_json_default, ensure_ascii=False)


async def _init_connection(conn: asyncpg.Connection) -> None:
    for type_name in ("json", "jsonb"):
        await conn.set_type_codec(
            type_name,
            encoder=to_json,
            decoder=json.loads,
            schema="pg_catalog",
        )


class Database:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=1,
            max_size=5,
            init=_init_connection,
            command_timeout=30,
        )

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    # -----------------------------------------------------
    # Generic helpers
    # -----------------------------------------------------
    async def fetchrow(self, sql: str, *args: Any) -> Optional[asyncpg.Record]:
        assert self.pool is not None
        return await self.pool.fetchrow(sql, *args)

    async def fetch(self, sql: str, *args: Any) -> list[asyncpg.Record]:
        assert self.pool is not None
        return await self.pool.fetch(sql, *args)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        assert self.pool is not None
        return await self.pool.fetchval(sql, *args)

    async def execute(self, sql: str, *args: Any) -> str:
        assert self.pool is not None
        return await self.pool.execute(sql, *args)

    async def executemany(self, sql: str, rows: list[tuple]) -> None:
        assert self.pool is not None
        if rows:
            await self.pool.executemany(sql, rows)

    # -----------------------------------------------------
    # Migrations (serialized across instances by an advisory lock)
    # -----------------------------------------------------
    async def migrate(self) -> None:
        assert self.pool is not None
        files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        async with self.pool.acquire() as conn:
            await conn.execute("SELECT pg_advisory_lock($1)", q.LOCK_MIGRATIONS)
            try:
                await conn.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations ("
                    "version TEXT PRIMARY KEY, "
                    "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
                )
                for path in files:
                    version = path.stem
                    if await conn.fetchval(q.MIGRATION_APPLIED, version):
                        continue
                    sql = path.read_text(encoding="utf-8")
                    async with conn.transaction():
                        await conn.execute(sql)
                        await conn.execute(q.MIGRATION_RECORD, version)
                    logger.info("Applied migration %s", version)
            finally:
                await conn.execute("SELECT pg_advisory_unlock($1)", q.LOCK_MIGRATIONS)

    # -----------------------------------------------------
    # Event ledger
    # -----------------------------------------------------
    async def claim_event(
        self,
        entity_type: str,
        entity_id: int,
        event_type: str,
        level: int = 0,
        payload: Optional[dict] = None,
        max_attempts: int = 3,
    ) -> Optional[int]:
        """Return an event id if this caller may perform the side effect.

        None means the event already happened (SENT/UNCERTAIN/RECORDED) or
        is in progress elsewhere: the caller must NOT send anything.
        """
        event_id = await self.fetchval(
            q.CLAIM_EVENT, entity_type, entity_id, event_type, level, payload or {}
        )
        if event_id is not None:
            return event_id
        return await self.fetchval(
            q.RECLAIM_FAILED_EVENT,
            entity_type,
            entity_id,
            event_type,
            level,
            max_attempts,
        )

    async def mark_event(
        self,
        event_id: int,
        status: str,
        message_id: Optional[int] = None,
        error: Optional[str] = None,
    ) -> None:
        await self.execute(q.MARK_EVENT, event_id, status, message_id, error)

    async def record_event(
        self,
        entity_type: str,
        entity_id: int,
        event_type: str,
        level: int = 0,
        payload: Optional[dict] = None,
    ) -> bool:
        return (
            await self.fetchval(
                q.RECORD_EVENT, entity_type, entity_id, event_type, level, payload or {}
            )
            is not None
        )


class LeaderLock:
    """Session-level advisory lock on a dedicated connection.

    Only the holder runs the UW watch engine, the tracker and the sweeps.
    If the process dies or the connection drops, PostgreSQL releases the
    lock automatically, so the next instance takes over.
    """

    def __init__(self, dsn: str, key: int = q.LOCK_LEADER) -> None:
        self._dsn = dsn
        self._key = key
        self._conn: Optional[asyncpg.Connection] = None

    @property
    def held(self) -> bool:
        return self._conn is not None and not self._conn.is_closed()

    async def try_acquire(self) -> bool:
        if self.held:
            return True
        conn = await asyncpg.connect(dsn=self._dsn, timeout=15)
        try:
            ok = await conn.fetchval("SELECT pg_try_advisory_lock($1)", self._key)
        except Exception:
            await conn.close()
            raise
        if ok:
            self._conn = conn
            return True
        await conn.close()
        return False

    async def still_held(self) -> bool:
        if not self.held:
            return False
        try:
            await self._conn.fetchval("SELECT 1", timeout=10)
            return True
        except Exception:
            await self.release()
            return False

    async def release(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None and not conn.is_closed():
            try:
                await conn.execute("SELECT pg_advisory_unlock($1)", self._key)
            except Exception:
                pass
            try:
                await conn.close()
            except Exception:
                pass
