"""
BrowserAgent — Memory Retrieval Engine (Phase 4)

Orchestrates retrieval across all memory stores to produce a ranked
list of the most relevant MemoryItems for a given task.

Priority order:
  1. user_rule type with matching domain
  2. site type with matching domain
  3. semantic type matching goal keywords
  4. Recent successful episodic runs matching domain

Returns at most 10 items.  Logs which memories were retrieved and why
for debugging / transparency.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from schemas import MemoryItem, MemoryType
from memory.db import MemoryDB
from memory.episodic_store import EpisodicStore
from memory.semantic_store import SemanticStore
from memory.site_profiles import SiteProfileStore
from memory.embeddings import compute_similarity

logger = logging.getLogger("browseragent.memory.retrieval")

MAX_RESULTS = 10


class MemoryRetriever:
    """Unified retrieval across episodic, semantic, and site stores."""

    def __init__(self, db: MemoryDB) -> None:
        self._db = db
        self.episodic = EpisodicStore(db)
        self.semantic = SemanticStore(db)
        self.sites    = SiteProfileStore(db)

    def get_context_for_task(
        self,
        goal: str,
        domain: Optional[str] = None,
        task_type: Optional[str] = None,
    ) -> list[MemoryItem]:
        """Retrieve the most relevant memories for a planning request.

        Collects candidates from all stores, assigns priority scores,
        de-duplicates, and returns the top items.

        Args:
            goal:      Natural-language goal text.
            domain:    Domain of the target page (e.g. "github.com").
            task_type: Page/task type hint (e.g. "quiz", "form", "login").

        Returns:
            Up to 10 MemoryItems ordered by priority.
        """
        logger.info(
            "Retrieving context — goal=%s, domain=%s, task_type=%s",
            goal[:60], domain, task_type,
        )

        scored: list[tuple[float, str, MemoryItem]] = []
        # Each entry: (score, source_label, MemoryItem)

        # ── 1. User rules matching domain (highest priority) ──────
        user_rules = self.semantic.get_rules(type_filter="user_rule", domain=domain)
        if not user_rules and domain:
            # Also try global user rules
            user_rules = self.semantic.get_rules(type_filter="user_rule")

        for rule in user_rules:
            base_score = 10.0  # Highest base priority

            # Domain match boost
            if domain and rule.domain and rule.domain.lower() == domain.lower():
                base_score += 3.0

            # Goal relevance
            sim = compute_similarity(goal, rule.instruction)
            final = base_score + sim * 2.0

            scored.append((final, "user_rule", rule))
            logger.debug("  [user_rule] %.2f — %s", final, rule.instruction[:60])

        # ── 2. Site rules matching domain ─────────────────────────
        site_rules = self.semantic.get_rules(type_filter="site", domain=domain)
        for rule in site_rules:
            base_score = 7.0
            sim = compute_similarity(goal, rule.instruction)
            final = base_score + sim * 2.0

            scored.append((final, "site", rule))
            logger.debug("  [site] %.2f — %s", final, rule.instruction[:60])

        # ── 3. Semantic rules matching goal keywords ──────────────
        semantic_rules = self.semantic.get_relevant_rules(
            goal=goal, domain=domain, limit=15
        )
        for rule in semantic_rules:
            # Skip user_rule and site types (already handled above)
            if rule.type in (MemoryType.USER_RULE, MemoryType.SITE):
                continue

            base_score = 4.0
            sim = compute_similarity(goal, rule.instruction)
            final = base_score + sim * 3.0

            # Task-type match bonus
            if task_type:
                task_sim = compute_similarity(task_type, rule.instruction)
                final += task_sim * 1.0

            scored.append((final, "semantic", rule))
            logger.debug("  [semantic] %.2f — %s", final, rule.instruction[:60])

        # ── 4. Episodic: recent successful runs ───────────────────
        similar_runs = self.episodic.get_similar_runs(
            goal_text=goal, limit=5, domain=domain
        )

        for run in similar_runs:
            if not run.get("success"):
                continue  # Only learn from successes

            base_score = 2.0
            relevance = run.get("_relevance_score", 0.0)
            final = base_score + relevance * 3.0

            # Convert run to a synthetic MemoryItem
            run_memory = MemoryItem(
                memory_id=f"episodic_{run.get('task_id', '')}",
                type=MemoryType.EPISODIC,
                scope=run.get("domain", "global") or "global",
                domain=run.get("domain"),
                instruction=f"Previously succeeded: {run.get('goal', '')}",
                trigger_conditions=[],
                preferred_actions=[],
                avoid_actions=[],
                confidence=1.0,
                success_count=1,
                failure_count=0,
            )

            scored.append((final, "episodic", run_memory))
            logger.debug("  [episodic] %.2f — %s", final, run.get("goal", "")[:60])

        # ── 5. Site profile as a synthetic memory item ────────────
        if domain:
            profile = self.sites.get_profile(domain)
            if profile:
                parts: list[str] = []
                if profile.get("next_button_patterns"):
                    parts.append(
                        f"Next buttons: {', '.join(profile['next_button_patterns'][:5])}"
                    )
                if profile.get("submit_button_patterns"):
                    parts.append(
                        f"Submit buttons: {', '.join(profile['submit_button_patterns'][:5])}"
                    )
                if profile.get("custom_notes"):
                    parts.append(f"Notes: {profile['custom_notes'][:200]}")

                if parts:
                    profile_memory = MemoryItem(
                        memory_id=f"site_profile_{domain}",
                        type=MemoryType.SITE,
                        scope=domain,
                        domain=domain,
                        instruction=f"Site knowledge for {domain}: "
                        + "; ".join(parts),
                        trigger_conditions=[f"user is on {domain}"],
                        preferred_actions=[],
                        avoid_actions=[],
                        confidence=1.0,
                        success_count=0,
                        failure_count=0,
                    )
                    scored.append((6.0, "site_profile", profile_memory))
                    logger.debug(
                        "  [site_profile] 6.00 — %s", profile_memory.instruction[:60]
                    )

        # ── De-duplicate and sort ─────────────────────────────────
        seen_ids: set[str] = set()
        unique: list[tuple[float, str, MemoryItem]] = []
        for score, source, item in scored:
            if item.memory_id not in seen_ids:
                seen_ids.add(item.memory_id)
                unique.append((score, source, item))

        unique.sort(key=lambda t: t[0], reverse=True)
        results = [item for _, _, item in unique[:MAX_RESULTS]]

        # ── Log summary ──────────────────────────────────────────
        logger.info(
            "Retrieved %d memories (from %d candidates)",
            len(results), len(scored),
        )
        for score, source, item in unique[:MAX_RESULTS]:
            logger.info(
                "  [%s] score=%.2f  %s — %s",
                source,
                score,
                item.type.value,
                item.instruction[:70],
            )

        return results
