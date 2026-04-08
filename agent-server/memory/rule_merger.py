"""
BrowserAgent — Rule Merger (Phase 7)

Deduplication, conflict detection, and merging logic for the rule store.

Functions:
  • compute_text_similarity()     — Jaccard word similarity
  • find_similar_rules()          — find duplicates above a threshold
  • are_conflicting()             — detect contradictory rules
  • merge_rules()                 — merge two compatible rules
  • suggest_consolidation()       — find all mergeable pairs
"""

from __future__ import annotations

import logging
import re
import string
from datetime import datetime, timezone
from typing import Any

from schemas import MemoryItem, MemoryType

logger = logging.getLogger("browseragent.memory.merger")


# ── Text Similarity ──────────────────────────────────────────────────


def _normalise_text(text: str) -> set[str]:
    """Lowercase, strip punctuation, split into word set."""
    text = text.lower()
    text = text.translate(str.maketrans("", "", string.punctuation))
    words = text.split()
    # Remove very common stop words that add noise
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "to", "of", "in", "on", "at", "for", "and", "or", "but",
        "it", "its", "this", "that", "with", "by", "as", "from",
    }
    return {w for w in words if w and w not in stop_words}


def compute_text_similarity(text1: str, text2: str) -> float:
    """Compute Jaccard similarity between two text strings.

    Both strings are normalised: lowercased, punctuation removed,
    split on whitespace. Stop words are excluded.

    Args:
        text1: First text string.
        text2: Second text string.

    Returns:
        0.0 to 1.0 similarity score.
        0.0 if either text is empty after normalisation.
    """
    words1 = _normalise_text(text1)
    words2 = _normalise_text(text2)

    if not words1 or not words2:
        return 0.0

    intersection = words1 & words2
    union = words1 | words2

    return len(intersection) / len(union) if union else 0.0


def _list_similarity(list_a: list[str], list_b: list[str]) -> float:
    """Compute similarity between two lists of strings.

    Combines all strings in each list into a single text and
    computes Jaccard similarity on the combined text.
    """
    combined_a = " ".join(list_a)
    combined_b = " ".join(list_b)
    return compute_text_similarity(combined_a, combined_b)


# ── Find Similar Rules ───────────────────────────────────────────────


def find_similar_rules(
    new_item: MemoryItem,
    existing_rules: list[MemoryItem],
    threshold: float = 0.6,
) -> list[MemoryItem]:
    """Find existing rules that are similar to new_item.

    Compares instruction text and trigger_conditions to determine
    similarity. Returns rules above the threshold, sorted by
    similarity score descending.

    Args:
        new_item:       The new MemoryItem to check against.
        existing_rules: All existing MemoryItems in the store.
        threshold:      Minimum similarity score to include (0.0-1.0).

    Returns:
        List of MemoryItems with similarity >= threshold, sorted
        by similarity descending.
    """
    if not existing_rules:
        return []

    scored: list[tuple[float, MemoryItem]] = []

    for existing in existing_rules:
        # Skip self
        if existing.memory_id == new_item.memory_id:
            continue

        # Instruction similarity (weighted 70%)
        inst_sim = compute_text_similarity(
            new_item.instruction, existing.instruction
        )

        # Trigger conditions similarity (weighted 30%)
        trigger_sim = _list_similarity(
            new_item.trigger_conditions, existing.trigger_conditions
        )

        combined = 0.7 * inst_sim + 0.3 * trigger_sim

        # Domain match bonus: +0.1 if same domain
        if new_item.domain and existing.domain:
            if new_item.domain.lower() == existing.domain.lower():
                combined += 0.1

        # Scope match bonus: +0.05 if same scope
        if new_item.scope == existing.scope:
            combined += 0.05

        combined = min(1.0, combined)

        if combined >= threshold:
            scored.append((combined, existing))

    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)
    return [rule for _, rule in scored]


# ── Conflict Detection ───────────────────────────────────────────────


def are_conflicting(rule_a: MemoryItem, rule_b: MemoryItem) -> bool:
    """Determine whether two rules are in conflict.

    Returns True if:
      1. rule_a's preferred_actions overlaps with rule_b's avoid_actions
         or vice versa.
      2. Both rules have the same scope + domain but contradictory
         instructions (similarity > 0.7 yet different preferred_actions).

    Args:
        rule_a: First MemoryItem to compare.
        rule_b: Second MemoryItem to compare.

    Returns:
        True if the rules are conflicting.
    """
    # Check cross-contamination: A's preferred ∩ B's avoid
    a_preferred_words = _normalise_text(" ".join(rule_a.preferred_actions))
    b_avoid_words = _normalise_text(" ".join(rule_b.avoid_actions))

    if a_preferred_words and b_avoid_words:
        overlap = a_preferred_words & b_avoid_words
        if len(overlap) >= 2:
            return True

    # Check reverse: B's preferred ∩ A's avoid
    b_preferred_words = _normalise_text(" ".join(rule_b.preferred_actions))
    a_avoid_words = _normalise_text(" ".join(rule_a.avoid_actions))

    if b_preferred_words and a_avoid_words:
        overlap = b_preferred_words & a_avoid_words
        if len(overlap) >= 2:
            return True

    # Check same scope + domain but contradictory preferred actions
    same_scope = rule_a.scope == rule_b.scope
    same_domain = (
        (rule_a.domain or "").lower() == (rule_b.domain or "").lower()
    )

    if same_scope and same_domain:
        inst_sim = compute_text_similarity(
            rule_a.instruction, rule_b.instruction
        )
        if inst_sim > 0.7:
            # Instructions are similar — check if preferred actions differ
            pref_sim = _list_similarity(
                rule_a.preferred_actions, rule_b.preferred_actions
            )
            if pref_sim < 0.3:
                # Similar instructions but very different recommended actions
                return True

    return False


# ── Rule Merging ─────────────────────────────────────────────────────


def merge_rules(base: MemoryItem, new_rule: MemoryItem) -> MemoryItem:
    """Merge new_rule into base, producing a combined MemoryItem.

    Strategy:
      • trigger_conditions: union, deduplicated
      • preferred_actions: union, deduplicated
      • avoid_actions: union, deduplicated
      • confidence: average of both
      • instruction: the longer (more specific) one wins
      • updated_at: set to now
      • success/failure counts: summed

    Args:
        base:     The existing rule to merge into.
        new_rule: The new rule being merged.

    Returns:
        A new MemoryItem combining both rules.
    """
    now = datetime.now(timezone.utc)

    # Choose the more specific (longer) instruction
    instruction = (
        new_rule.instruction
        if len(new_rule.instruction) > len(base.instruction)
        else base.instruction
    )

    # Union and deduplicate lists (preserve order)
    triggers = _deduplicated_union(
        base.trigger_conditions, new_rule.trigger_conditions
    )
    preferred = _deduplicated_union(
        base.preferred_actions, new_rule.preferred_actions
    )
    avoid = _deduplicated_union(
        base.avoid_actions, new_rule.avoid_actions
    )

    # Average confidence
    avg_confidence = round((base.confidence + new_rule.confidence) / 2.0, 4)

    # Prefer the more specific scope
    scope = base.scope
    if new_rule.scope != "global" and base.scope == "global":
        scope = new_rule.scope

    # Prefer non-null domain
    domain = base.domain or new_rule.domain

    # Resolve type
    mem_type = base.type
    if isinstance(new_rule.type, MemoryType) and new_rule.type == MemoryType.SITE:
        mem_type = MemoryType.SITE
    elif isinstance(new_rule.type, MemoryType) and new_rule.type == MemoryType.USER_RULE:
        if isinstance(base.type, MemoryType) and base.type == MemoryType.SEMANTIC:
            mem_type = MemoryType.USER_RULE

    return MemoryItem(
        memory_id=base.memory_id,  # keep the base ID
        type=mem_type,
        scope=scope,
        domain=domain,
        instruction=instruction,
        trigger_conditions=triggers,
        preferred_actions=preferred,
        avoid_actions=avoid,
        confidence=avg_confidence,
        success_count=base.success_count + new_rule.success_count,
        failure_count=base.failure_count + new_rule.failure_count,
        created_at=base.created_at,
        updated_at=now,
    )


def _deduplicated_union(
    list_a: list[str], list_b: list[str]
) -> list[str]:
    """Merge two lists, removing near-duplicates.

    Two strings are considered duplicates if their normalised word
    sets have Jaccard similarity > 0.8.
    """
    result: list[str] = list(list_a)  # start with a copy of list_a

    for item_b in list_b:
        is_duplicate = False
        for item_a in result:
            if compute_text_similarity(item_a, item_b) > 0.8:
                is_duplicate = True
                break
        if not is_duplicate:
            result.append(item_b)

    return result


# ── Consolidation Suggestions ────────────────────────────────────────


def suggest_consolidation(
    rules: list[MemoryItem],
) -> list[tuple[MemoryItem, MemoryItem, float]]:
    """Find all pairs of rules that could be merged.

    Returns pairs with instruction similarity > 0.6, sorted by
    similarity descending.

    Args:
        rules: All rules in the store.

    Returns:
        List of (rule_a, rule_b, similarity_score) tuples.
    """
    if len(rules) < 2:
        return []

    pairs: list[tuple[MemoryItem, MemoryItem, float]] = []
    seen: set[tuple[str, str]] = set()

    for i, rule_a in enumerate(rules):
        for j, rule_b in enumerate(rules):
            if i >= j:
                continue

            pair_key = (rule_a.memory_id, rule_b.memory_id)
            if pair_key in seen:
                continue
            seen.add(pair_key)

            sim = compute_text_similarity(
                rule_a.instruction, rule_b.instruction
            )

            # Also factor in trigger overlap
            trigger_sim = _list_similarity(
                rule_a.trigger_conditions, rule_b.trigger_conditions
            )
            combined = 0.7 * sim + 0.3 * trigger_sim

            if combined > 0.6:
                pairs.append((rule_a, rule_b, round(combined, 4)))

    # Sort by similarity descending
    pairs.sort(key=lambda x: x[2], reverse=True)

    logger.info(
        "suggest_consolidation: %d rules → %d mergeable pairs",
        len(rules), len(pairs),
    )

    return pairs
