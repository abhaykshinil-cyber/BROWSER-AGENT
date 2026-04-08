"""
BrowserAgent — Semantic Store (Phase 4)

Stores and retrieves reusable rules and behavioural patterns.
Each rule is a MemoryItem-shaped record persisted in the
``memory_rules`` table.

Relevance ranking uses the lightweight keyword-similarity scorer
from ``memory.embeddings`` so no external vector DB is needed.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from schemas import MemoryItem, MemoryType
from memory.db import MemoryDB
from memory.embeddings import compute_multi_field_similarity

logger = logging.getLogger("browseragent.memory.semantic")


class SemanticStore:
    """Reusable rules and patterns backed by ``memory_rules``."""

    def __init__(self, db: MemoryDB) -> None:
        self._db = db

    # ── Write ─────────────────────────────────────────────────────

    def save_rule(self, item: MemoryItem) -> str:
        """Persist a MemoryItem as a rule.  Returns the memory_id."""
        now = datetime.now(timezone.utc).isoformat()
        memory_id = item.memory_id or uuid.uuid4().hex
        mem_type = item.type.value if isinstance(item.type, MemoryType) else item.type

        self._db.run_execute(
            """
            INSERT OR REPLACE INTO memory_rules
                (memory_id, type, scope, domain, instruction,
                 trigger_conditions_json, preferred_actions_json,
                 avoid_actions_json, confidence,
                 success_count, failure_count,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                mem_type,
                item.scope,
                item.domain,
                item.instruction,
                json.dumps(item.trigger_conditions),
                json.dumps(item.preferred_actions),
                json.dumps(item.avoid_actions),
                item.confidence,
                item.success_count,
                item.failure_count,
                item.created_at.isoformat()
                if isinstance(item.created_at, datetime)
                else now,
                now,
            ),
        )

        logger.info(
            "Saved rule %s (type=%s, scope=%s, domain=%s)",
            memory_id, mem_type, item.scope, item.domain,
        )
        return memory_id

    # ── Read ──────────────────────────────────────────────────────

    def get_rules(
        self,
        type_filter: Optional[str] = None,
        domain: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> list[MemoryItem]:
        """List rules with optional filters.  Ordered by updated_at DESC."""
        clauses: list[str] = []
        params: list[Any] = []

        if type_filter:
            clauses.append("type = ?")
            params.append(type_filter)
        if domain:
            clauses.append("domain = ?")
            params.append(domain)
        if scope:
            clauses.append("scope = ?")
            params.append(scope)

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._db.run_query(
            f"SELECT * FROM memory_rules{where} ORDER BY updated_at DESC",
            tuple(params),
        )

        return [_row_to_memory_item(row) for row in rows]

    def get_relevant_rules(
        self,
        goal: str,
        domain: Optional[str] = None,
        limit: int = 10,
    ) -> list[MemoryItem]:
        """Find rules most relevant to the given goal and domain.

        Scoring uses multi-field keyword similarity across ``instruction``,
        ``trigger_conditions``, and ``preferred_actions``.  Domain-matched
        rules get a boost.

        Returns up to ``limit`` items ordered by relevance.
        """
        # Fetch all candidates (or domain-scoped if provided)
        candidates = self._db.run_query(
            "SELECT * FROM memory_rules ORDER BY updated_at DESC LIMIT 200"
        )

        if not candidates:
            return []

        # Score each candidate
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in candidates:
            # Multi-field similarity
            sim = compute_multi_field_similarity(
                query=goal,
                item=row,
                fields=["instruction", "trigger_conditions_json", "preferred_actions_json"],
                weights=[3.0, 2.0, 1.0],
            )

            # Domain boost: +0.3 if the rule's domain matches
            if domain and row.get("domain"):
                if row["domain"].lower() == domain.lower():
                    sim += 0.3
                elif domain.lower().endswith("." + row["domain"].lower()):
                    sim += 0.15

            # Type boost: user_rule and site types are more authoritative
            rule_type = row.get("type", "")
            if rule_type == "user_rule":
                sim += 0.2
            elif rule_type == "site":
                sim += 0.1

            # Confidence weighting
            confidence = row.get("confidence", 1.0)
            sim *= (0.5 + 0.5 * confidence)

            scored.append((sim, row))

        # Sort descending and take top N
        scored.sort(key=lambda pair: pair[0], reverse=True)
        top = scored[:limit]

        results: list[MemoryItem] = []
        for score, row in top:
            if score <= 0.01:
                continue  # Skip zero-relevance noise
            item = _row_to_memory_item(row)
            results.append(item)
            logger.debug(
                "  Relevant rule: %.3f  %s — %s",
                score, item.type.value, item.instruction[:80],
            )

        logger.info(
            "get_relevant_rules(goal=%s, domain=%s) → %d results",
            goal[:50], domain, len(results),
        )

        return results

    # ── Update ────────────────────────────────────────────────────

    def update_rule(self, memory_id: str, updates: dict[str, Any]) -> None:
        """Apply partial updates to a rule.

        ``updates`` may contain any of: instruction, confidence, scope,
        domain, trigger_conditions, preferred_actions, avoid_actions.
        """
        if not updates:
            return

        sets: list[str] = ["updated_at = ?"]
        params: list[Any] = [datetime.now(timezone.utc).isoformat()]

        field_map = {
            "instruction":        "instruction",
            "confidence":         "confidence",
            "scope":              "scope",
            "domain":             "domain",
            "trigger_conditions": "trigger_conditions_json",
            "preferred_actions":  "preferred_actions_json",
            "avoid_actions":      "avoid_actions_json",
        }

        for key, column in field_map.items():
            if key in updates:
                value = updates[key]
                if isinstance(value, (list, dict)):
                    value = json.dumps(value)
                sets.append(f"{column} = ?")
                params.append(value)

        params.append(memory_id)
        self._db.run_execute(
            f"UPDATE memory_rules SET {', '.join(sets)} WHERE memory_id = ?",
            tuple(params),
        )
        logger.info("Updated rule %s", memory_id)

    def record_outcome(self, memory_id: str, success: bool) -> None:
        """Increment the success or failure count and recalculate confidence.

        Confidence is a simple Bayesian-style estimate:
            confidence = (success_count + 1) / (success_count + failure_count + 2)
        This gives a prior of 0.5 with no data and converges toward the
        true success rate as data accumulates.
        """
        if success:
            self._db.run_execute(
                "UPDATE memory_rules SET success_count = success_count + 1, "
                "updated_at = ? WHERE memory_id = ?",
                (datetime.now(timezone.utc).isoformat(), memory_id),
            )
        else:
            self._db.run_execute(
                "UPDATE memory_rules SET failure_count = failure_count + 1, "
                "updated_at = ? WHERE memory_id = ?",
                (datetime.now(timezone.utc).isoformat(), memory_id),
            )

        # Recalculate confidence
        rows = self._db.run_query(
            "SELECT success_count, failure_count FROM memory_rules WHERE memory_id = ?",
            (memory_id,),
        )
        if rows:
            s = rows[0]["success_count"]
            f = rows[0]["failure_count"]
            new_confidence = (s + 1) / (s + f + 2)
            self._db.run_execute(
                "UPDATE memory_rules SET confidence = ?, updated_at = ? WHERE memory_id = ?",
                (round(new_confidence, 4), datetime.now(timezone.utc).isoformat(), memory_id),
            )

        logger.info(
            "Recorded %s for rule %s",
            "success" if success else "failure",
            memory_id,
        )

    # ── Delete ────────────────────────────────────────────────────

    def delete_rule(self, memory_id: str) -> None:
        """Delete a rule by its memory_id."""
        deleted = self._db.run_execute(
            "DELETE FROM memory_rules WHERE memory_id = ?", (memory_id,)
        )
        if deleted:
            logger.info("Deleted rule %s", memory_id)
        else:
            logger.warning("Rule %s not found for deletion", memory_id)


# ── Row conversion ────────────────────────────────────────────────────

def _row_to_memory_item(row: dict[str, Any]) -> MemoryItem:
    """Convert a memory_rules row dict to a MemoryItem."""
    raw_type = row.get("type", "semantic")
    try:
        mem_type = MemoryType(raw_type)
    except ValueError:
        mem_type = MemoryType.SEMANTIC

    return MemoryItem(
        memory_id=row["memory_id"],
        type=mem_type,
        scope=row.get("scope", "global"),
        domain=row.get("domain"),
        instruction=row["instruction"],
        trigger_conditions=json.loads(row.get("trigger_conditions_json", "[]")),
        preferred_actions=json.loads(row.get("preferred_actions_json", "[]")),
        avoid_actions=json.loads(row.get("avoid_actions_json", "[]")),
        confidence=row.get("confidence", 1.0),
        success_count=row.get("success_count", 0),
        failure_count=row.get("failure_count", 0),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
