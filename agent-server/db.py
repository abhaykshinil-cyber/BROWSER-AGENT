"""
BrowserAgent — SQLite Database Layer

Async SQLite access via aiosqlite.  Provides CRUD for memory items and
a simple run-history table.  All dates are stored as ISO-8601 UTC strings.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from schemas import MemoryItem, MemoryType

logger = logging.getLogger("browseragent.db")

# ── Schema SQL ────────────────────────────────────────────────────────

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS memories (
    memory_id        TEXT PRIMARY KEY,
    type             TEXT NOT NULL,
    scope            TEXT NOT NULL DEFAULT 'global',
    domain           TEXT,
    instruction      TEXT NOT NULL,
    trigger_conditions TEXT NOT NULL DEFAULT '[]',
    preferred_actions  TEXT NOT NULL DEFAULT '[]',
    avoid_actions      TEXT NOT NULL DEFAULT '[]',
    confidence       REAL NOT NULL DEFAULT 1.0,
    success_count    INTEGER NOT NULL DEFAULT 0,
    failure_count    INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_history (
    task_id      TEXT PRIMARY KEY,
    goal         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    steps_json   TEXT NOT NULL DEFAULT '[]',
    result_json  TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_mem_type   ON memories(type);
CREATE INDEX IF NOT EXISTS idx_mem_domain ON memories(domain);
CREATE INDEX IF NOT EXISTS idx_mem_scope  ON memories(scope);
"""


class Database:
    """Thin async wrapper around a single SQLite file."""

    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        """Open (or create) the database and run schema migrations."""
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_INIT_SQL)
        await self._conn.commit()
        logger.info("Database connected: %s", self._path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("Database closed")

    # ── Memory CRUD ───────────────────────────────────────────────────

    async def save_memory(self, item: MemoryItem) -> MemoryItem:
        """Insert or replace a memory item."""
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO memories
                (memory_id, type, scope, domain, instruction,
                 trigger_conditions, preferred_actions, avoid_actions,
                 confidence, success_count, failure_count,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.memory_id,
                item.type.value if isinstance(item.type, MemoryType) else item.type,
                item.scope,
                item.domain,
                item.instruction,
                json.dumps(item.trigger_conditions),
                json.dumps(item.preferred_actions),
                json.dumps(item.avoid_actions),
                item.confidence,
                item.success_count,
                item.failure_count,
                item.created_at.isoformat() if isinstance(item.created_at, datetime) else now,
                now,
            ),
        )
        await self._conn.commit()
        return item

    async def get_memory(self, memory_id: str) -> Optional[MemoryItem]:
        """Fetch a single memory by ID."""
        cursor = await self._conn.execute(
            "SELECT * FROM memories WHERE memory_id = ?", (memory_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return _row_to_memory(row)

    async def list_memories(
        self,
        type_filter: Optional[str] = None,
        domain_filter: Optional[str] = None,
        scope_filter: Optional[str] = None,
    ) -> list[MemoryItem]:
        """List memories with optional filters."""
        clauses: list[str] = []
        params: list[str] = []

        if type_filter:
            clauses.append("type = ?")
            params.append(type_filter)
        if domain_filter:
            clauses.append("domain = ?")
            params.append(domain_filter)
        if scope_filter:
            clauses.append("scope = ?")
            params.append(scope_filter)

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        cursor = await self._conn.execute(
            f"SELECT * FROM memories{where} ORDER BY updated_at DESC", params
        )
        rows = await cursor.fetchall()
        return [_row_to_memory(r) for r in rows]

    async def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory item by ID.  Returns True if it existed."""
        cursor = await self._conn.execute(
            "DELETE FROM memories WHERE memory_id = ?", (memory_id,)
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def update_memory(
        self,
        memory_id: str,
        *,
        instruction: Optional[str] = None,
        confidence: Optional[float] = None,
        success_delta: int = 0,
        failure_delta: int = 0,
    ) -> Optional[MemoryItem]:
        """Partially update a memory item."""
        sets: list[str] = ["updated_at = ?"]
        params: list = [datetime.now(timezone.utc).isoformat()]

        if instruction is not None:
            sets.append("instruction = ?")
            params.append(instruction)
        if confidence is not None:
            sets.append("confidence = ?")
            params.append(confidence)
        if success_delta:
            sets.append("success_count = success_count + ?")
            params.append(success_delta)
        if failure_delta:
            sets.append("failure_count = failure_count + ?")
            params.append(failure_delta)

        params.append(memory_id)
        await self._conn.execute(
            f"UPDATE memories SET {', '.join(sets)} WHERE memory_id = ?",
            params,
        )
        await self._conn.commit()
        return await self.get_memory(memory_id)


def _row_to_memory(row) -> MemoryItem:
    """Convert a sqlite3.Row to a MemoryItem."""
    return MemoryItem(
        memory_id=row["memory_id"],
        type=MemoryType(row["type"]),
        scope=row["scope"],
        domain=row["domain"],
        instruction=row["instruction"],
        trigger_conditions=json.loads(row["trigger_conditions"]),
        preferred_actions=json.loads(row["preferred_actions"]),
        avoid_actions=json.loads(row["avoid_actions"]),
        confidence=row["confidence"],
        success_count=row["success_count"],
        failure_count=row["failure_count"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
