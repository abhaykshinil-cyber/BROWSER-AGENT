"""
BrowserAgent — Memory Database Layer (Phase 4)

Synchronous SQLite connection and schema initialisation for the
memory subsystem.  Uses the standard-library ``sqlite3`` module only.

All three memory tables live in the same database file as the Phase 3
``memories`` and ``task_history`` tables (they share ``agent.db``).
New tables are created with IF NOT EXISTS so they layer on safely.

Usage:
    from memory.db import MemoryDB

    mdb = MemoryDB("./database/agent.db")
    mdb.init()
    rows = mdb.run_query("SELECT * FROM task_runs WHERE domain = ?", ("github.com",))
    mdb.close()
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any, Optional

logger = logging.getLogger("browseragent.memory.db")

# ── Schema ────────────────────────────────────────────────────────────

_SCHEMA_SQL = """

-- Episodic task-run history
CREATE TABLE IF NOT EXISTS task_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      TEXT    NOT NULL UNIQUE,
    goal         TEXT    NOT NULL,
    domain       TEXT,
    steps_json   TEXT    NOT NULL DEFAULT '[]',
    results_json TEXT    NOT NULL DEFAULT '[]',
    success      INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_domain  ON task_runs(domain);
CREATE INDEX IF NOT EXISTS idx_runs_success ON task_runs(success);
CREATE INDEX IF NOT EXISTS idx_runs_created ON task_runs(created_at DESC);

-- Semantic / user rule memory store
CREATE TABLE IF NOT EXISTS memory_rules (
    memory_id              TEXT PRIMARY KEY,
    type                   TEXT NOT NULL DEFAULT 'semantic',
    scope                  TEXT NOT NULL DEFAULT 'global',
    domain                 TEXT,
    instruction            TEXT NOT NULL,
    trigger_conditions_json TEXT NOT NULL DEFAULT '[]',
    preferred_actions_json  TEXT NOT NULL DEFAULT '[]',
    avoid_actions_json      TEXT NOT NULL DEFAULT '[]',
    confidence             REAL    NOT NULL DEFAULT 1.0,
    success_count          INTEGER NOT NULL DEFAULT 0,
    failure_count          INTEGER NOT NULL DEFAULT 0,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rules_type   ON memory_rules(type);
CREATE INDEX IF NOT EXISTS idx_rules_domain ON memory_rules(domain);
CREATE INDEX IF NOT EXISTS idx_rules_scope  ON memory_rules(scope);

-- Per-domain site profiles
CREATE TABLE IF NOT EXISTS site_profiles (
    domain                 TEXT PRIMARY KEY,
    next_button_patterns   TEXT NOT NULL DEFAULT '[]',
    submit_button_patterns TEXT NOT NULL DEFAULT '[]',
    mcq_selectors          TEXT NOT NULL DEFAULT '[]',
    custom_notes           TEXT NOT NULL DEFAULT '',
    last_updated           TEXT NOT NULL
);

"""


class MemoryDB:
    """Synchronous SQLite wrapper for the memory subsystem.

    Uses ``sqlite3.Row`` row factory so results can be accessed by
    column name.  All write operations auto-commit.
    """

    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    # ── Lifecycle ─────────────────────────────────────────────────

    def init(self) -> None:
        """Open the database and create tables if needed."""
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()
        logger.info("MemoryDB initialised: %s", self._path)

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("MemoryDB closed")

    def get_connection(self) -> sqlite3.Connection:
        """Return the raw sqlite3.Connection (with Row factory)."""
        if self._conn is None:
            raise RuntimeError("MemoryDB not initialised — call init() first")
        return self._conn

    # ── Query helpers ─────────────────────────────────────────────

    def run_query(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        """Execute a SELECT and return results as a list of dicts."""
        cursor = self._conn.execute(sql, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def run_execute(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> int:
        """Execute an INSERT / UPDATE / DELETE and return rowcount."""
        cursor = self._conn.execute(sql, params)
        self._conn.commit()
        return cursor.rowcount

    def run_insert(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> int:
        """Execute an INSERT and return lastrowid."""
        cursor = self._conn.execute(sql, params)
        self._conn.commit()
        return cursor.lastrowid
