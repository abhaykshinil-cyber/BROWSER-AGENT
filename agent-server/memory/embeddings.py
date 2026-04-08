"""
BrowserAgent — Lightweight Text Similarity (Phase 4)

Keyword-based similarity scoring using word overlap and TF-IDF-style
weighting.  This avoids needing an external vector database or
embedding API for the early build while still providing useful
relevance ranking.

All functions are pure, stateless, and use only the standard library.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

# ── Stop words (compact set — enough to filter noise) ─────────────────

_STOP_WORDS: set[str] = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "is", "it", "be", "as", "do", "so", "if", "no",
    "not", "am", "are", "was", "were", "has", "had", "have", "will",
    "would", "could", "should", "shall", "may", "might", "can", "this",
    "that", "these", "those", "i", "me", "my", "we", "our", "you", "your",
    "he", "she", "them", "they", "its", "his", "her", "from", "up", "out",
    "about", "into", "over", "after", "then", "than", "just", "also",
    "very", "too", "all", "any", "each", "every", "some", "how", "what",
    "when", "where", "which", "who", "whom", "why",
}


# ── Tokeniser ─────────────────────────────────────────────────────────

def _tokenise(text: str) -> list[str]:
    """Lowercase, strip punctuation, remove stop words."""
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [t for t in tokens if t not in _STOP_WORDS and len(t) > 1]


# ── Core similarity ──────────────────────────────────────────────────

def compute_similarity(text1: str, text2: str) -> float:
    """Compute a 0.0–1.0 similarity score between two text strings.

    Uses a combination of:
      • Jaccard index (word-set overlap)
      • Weighted overlap (TF-IDF-style IDF boost for rarer words)

    Both components are averaged for the final score.

    Returns 0.0 if either text is empty after tokenisation.
    """
    tokens1 = _tokenise(text1)
    tokens2 = _tokenise(text2)

    if not tokens1 or not tokens2:
        return 0.0

    set1 = set(tokens1)
    set2 = set(tokens2)

    # ── Jaccard index ─────────────────────────────────────────────
    intersection = set1 & set2
    union = set1 | set2
    jaccard = len(intersection) / len(union) if union else 0.0

    # ── Weighted overlap (IDF-style) ──────────────────────────────
    # Words that appear in fewer documents (here: the two texts)
    # are more informative.  Since we only have two "documents",
    # a word appearing in both gets IDF ~ 0, so we approximate:
    #   - word in both texts → weight 1.0
    #   - weight boosted by inverse frequency within each text
    freq1 = Counter(tokens1)
    freq2 = Counter(tokens2)
    total1 = len(tokens1)
    total2 = len(tokens2)

    weighted_score = 0.0
    max_possible = 0.0

    all_words = set1 | set2
    for word in all_words:
        tf1 = freq1.get(word, 0) / total1
        tf2 = freq2.get(word, 0) / total2

        # IDF approximation: rarer words (lower mean TF) get higher weight
        mean_tf = (tf1 + tf2) / 2
        idf = 1.0 + math.log(1.0 / (mean_tf + 0.01))

        weight = idf
        max_possible += weight

        if word in intersection:
            # Score proportional to how present the word is in both texts
            overlap_strength = min(tf1, tf2) / max(tf1, tf2) if max(tf1, tf2) > 0 else 0
            weighted_score += weight * (0.5 + 0.5 * overlap_strength)

    weighted_ratio = weighted_score / max_possible if max_possible > 0 else 0.0

    # Average the two signals
    return round((jaccard + weighted_ratio) / 2.0, 4)


# ── Batch ranking ─────────────────────────────────────────────────────

def rank_by_relevance(
    query: str,
    items: list[dict[str, Any]],
    text_field: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Rank a list of dicts by their similarity to ``query``.

    Scores each item by computing ``compute_similarity(query, item[text_field])``,
    attaches a ``_relevance_score`` key, sorts descending, and returns
    the top ``limit`` items.

    Items whose text_field is empty or missing are scored 0.0.
    """
    scored: list[tuple[float, dict[str, Any]]] = []

    for item in items:
        text = item.get(text_field, "")
        if not text:
            scored.append((0.0, item))
            continue

        score = compute_similarity(query, str(text))
        item_copy = dict(item)
        item_copy["_relevance_score"] = score
        scored.append((score, item_copy))

    scored.sort(key=lambda pair: pair[0], reverse=True)

    return [pair[1] for pair in scored[:limit]]


# ── Multi-field similarity (for richer matching) ──────────────────────

def compute_multi_field_similarity(
    query: str,
    item: dict[str, Any],
    fields: list[str],
    weights: list[float] | None = None,
) -> float:
    """Compute weighted similarity across multiple fields of an item.

    Useful when a memory item has both ``instruction`` and
    ``trigger_conditions`` that should contribute to relevance.

    ``weights`` defaults to equal weight for all fields.
    """
    if not fields:
        return 0.0

    if weights is None:
        weights = [1.0] * len(fields)

    total_weight = sum(weights)
    if total_weight == 0:
        return 0.0

    score = 0.0
    for field, weight in zip(fields, weights):
        value = item.get(field, "")
        if isinstance(value, list):
            value = " ".join(str(v) for v in value)
        elif not isinstance(value, str):
            value = str(value) if value else ""

        if value:
            score += weight * compute_similarity(query, value)

    return round(score / total_weight, 4)
