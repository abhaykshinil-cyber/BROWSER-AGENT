"""
BrowserAgent — Episodic Store (Phase 4)

Stores and retrieves task-run history so the agent can learn from
past successes and failures.  Each run is a single row capturing the
goal, domain, executed steps, results, and outcome.

All I/O goes through the shared MemoryDB connection.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

from memory.db import MemoryDB
from memory.embeddings import rank_by_relevance

logger = logging.getLogger("browseragent.memory.episodic")


def _extract_domain(text: str) -> Optional[str]:
    """Try to pull a domain from a URL or freeform text."""
    url_match = re.search(r"https?://([^/\s]+)", text)
    if url_match:
        return url_match.group(1).lower()
    domain_match = re.search(r"(?:on|at|for)\s+([\w.-]+\.(?:com|org|net|io|dev|co|edu))", text, re.I)
    if domain_match:
        return domain_match.group(1).lower()
    return None


class EpisodicStore:
    """Task-run history backed by the ``task_runs`` table."""

    def __init__(self, db: MemoryDB) -> None:
        self._db = db

    # ── Write ─────────────────────────────────────────────────────

    def save_run(
        self,
        task_id: str,
        goal: str,
        steps: list[dict[str, Any]],
        results: list[dict[str, Any]],
        success: bool,
        domain: Optional[str] = None,
    ) -> None:
        """Persist a completed task run.

        If ``domain`` is not provided, an attempt is made to extract it
        from the goal text.
        """
        if domain is None:
            domain = _extract_domain(goal)

        now = datetime.now(timezone.utc).isoformat()

        self._db.run_execute(
            """
            INSERT OR REPLACE INTO task_runs
                (task_id, goal, domain, steps_json, results_json, success, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                goal,
                domain,
                json.dumps(steps, default=str),
                json.dumps(results, default=str),
                1 if success else 0,
                now,
            ),
        )
        logger.info(
            "Saved run %s (success=%s, domain=%s)", task_id, success, domain
        )

    # ── Read ──────────────────────────────────────────────────────

    def get_runs(
        self,
        limit: int = 20,
        domain: Optional[str] = None,
        success_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Return recent task runs, optionally filtered by domain.

        Results are ordered by ``created_at DESC``.
        """
        clauses: list[str] = []
        params: list[Any] = []

        if domain:
            clauses.append("domain = ?")
            params.append(domain)
        if success_only:
            clauses.append("success = 1")

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)

        rows = self._db.run_query(
            f"SELECT * FROM task_runs{where} ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )

        # Deserialise JSON columns
        for row in rows:
            row["steps"] = json.loads(row.get("steps_json", "[]"))
            row["results"] = json.loads(row.get("results_json", "[]"))
            row["success"] = bool(row.get("success", 0))

        return rows

    def get_similar_runs(
        self,
        goal_text: str,
        limit: int = 5,
        domain: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Find past runs whose goal is textually similar to ``goal_text``.

        Uses the lightweight keyword-similarity scorer from
        ``memory.embeddings``.  Optionally restricts to a specific domain.
        """
        # Fetch a broad candidate set (up to 100)
        candidates = self.get_runs(limit=100, domain=domain)

        if not candidates:
            return []

        ranked = rank_by_relevance(
            query=goal_text,
            items=candidates,
            text_field="goal",
            limit=limit,
        )

        logger.debug(
            "get_similar_runs(%s) → %d results (top score: %.3f)",
            goal_text[:60],
            len(ranked),
            ranked[0].get("_relevance_score", 0) if ranked else 0,
        )

        return ranked

    # ── Delete ────────────────────────────────────────────────────

    def delete_run(self, task_id: str) -> None:
        """Delete a task run by its task_id."""
        deleted = self._db.run_execute(
            "DELETE FROM task_runs WHERE task_id = ?", (task_id,)
        )
        if deleted:
            logger.info("Deleted run %s", task_id)
        else:
            logger.warning("Run %s not found for deletion", task_id)

    # ── Stats ─────────────────────────────────────────────────────

    def get_success_rate(self, domain: Optional[str] = None) -> dict[str, Any]:
        """Return aggregate success/failure counts, optionally by domain."""
        if domain:
            rows = self._db.run_query(
                "SELECT success, COUNT(*) AS cnt FROM task_runs WHERE domain = ? GROUP BY success",
                (domain,),
            )
        else:
            rows = self._db.run_query(
                "SELECT success, COUNT(*) AS cnt FROM task_runs GROUP BY success"
            )

        totals = {0: 0, 1: 0}
        for row in rows:
            totals[row["success"]] = row["cnt"]

        total = totals[0] + totals[1]
        return {
            "domain": domain,
            "total": total,
            "successes": totals[1],
            "failures": totals[0],
            "success_rate": round(totals[1] / total, 3) if total > 0 else 0.0,
        }
