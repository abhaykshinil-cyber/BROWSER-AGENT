"""
BrowserAgent — Multi-Tab Context Builder (Phase 9)

Server-side processor that takes raw tab data from the Chrome
extension, extracts cross-tab facts relevant to the user's goal,
and produces a compact multi-tab context for the planner prompt.

No external NLP libraries — uses standard-library text processing.
"""

from __future__ import annotations

import logging
import re
import string
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger("browseragent.core.tab_context")


# ── Pydantic Models ──────────────────────────────────────────────────


class MultiTabContext(BaseModel):
    """Complete multi-tab context ready for the planner."""

    active_tab: dict = Field(
        default_factory=dict,
        description="Full tab info (DOM summary, page type, questions) for the active tab.",
    )
    supporting_tabs: list[dict] = Field(
        default_factory=list,
        description="Compact summaries of other open tabs.",
    )
    total_tabs_open: int = Field(
        default=0,
        description="Total number of browser tabs open.",
    )
    cross_tab_facts: list[str] = Field(
        default_factory=list,
        description="Sentences from other tabs that are relevant to the current goal.",
    )
    context_summary: str = Field(
        default="",
        description="Multi-line summary of all open tabs for the planner.",
    )


# ── Stop Words ───────────────────────────────────────────────────────

_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "shall", "may", "might", "can", "must",
    "to", "of", "in", "on", "at", "for", "with", "by", "from", "as",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "under", "over",
    "and", "or", "but", "nor", "not", "no", "so", "yet", "both",
    "it", "its", "this", "that", "these", "those", "they", "them",
    "he", "she", "we", "you", "i", "me", "my", "your", "our", "their",
    "what", "which", "who", "whom", "how", "where", "when", "why",
    "all", "each", "every", "any", "some", "such",
    "if", "then", "than", "also", "very", "just", "about",
})


# ── Main Builder ─────────────────────────────────────────────────────


def build_multi_tab_context(
    tabs_data: list[dict],
    goal: str,
) -> MultiTabContext:
    """Build a MultiTabContext from raw tab data sent by the extension.

    Args:
        tabs_data: List of tab dicts from the extension.  Each may have:
                   tabId, title, url, page_type, question_count,
                   interactive_element_count, body_text_preview, error.
        goal:      The user's natural-language task goal.

    Returns:
        Populated MultiTabContext.
    """
    if not tabs_data:
        return MultiTabContext()

    # Separate active tab from supporting
    active_tab: dict = {}
    supporting: list[dict] = []

    for tab in tabs_data:
        if tab.get("active") or tab.get("is_active"):
            active_tab = tab
        else:
            supporting.append(tab)

    # If no tab explicitly active, use the first one
    if not active_tab and tabs_data:
        active_tab = tabs_data[0]
        supporting = tabs_data[1:]

    # Extract cross-tab facts
    tab_texts: list[tuple[str, str]] = []  # (tab_title, text)
    for tab in supporting:
        text = tab.get("body_text_preview", "") or ""
        title = tab.get("title", "") or ""
        if text.strip():
            tab_texts.append((title, text))

    cross_tab_facts = extract_relevant_facts(tab_texts, goal)

    # Summarise all tabs
    all_tabs_for_summary = [active_tab] + supporting
    context_summary = summarize_tabs(all_tabs_for_summary, active_tab.get("tabId"))

    return MultiTabContext(
        active_tab=active_tab,
        supporting_tabs=supporting,
        total_tabs_open=len(tabs_data),
        cross_tab_facts=cross_tab_facts,
        context_summary=context_summary,
    )


# ── Fact Extraction ──────────────────────────────────────────────────


def extract_relevant_facts(
    tab_texts: list[tuple[str, str]],
    goal: str,
    max_facts: int = 10,
) -> list[str]:
    """Find sentences from tab texts that share keywords with the goal.

    Args:
        tab_texts: List of (tab_title, body_text) tuples.
        goal:      The user's task goal.
        max_facts: Maximum number of facts to return.

    Returns:
        Formatted fact strings like 'From [Tab Title]: [sentence]'.
    """
    goal_words = _extract_keywords(goal)
    if not goal_words:
        return []

    scored: list[tuple[float, str]] = []

    for tab_title, text in tab_texts:
        sentences = _split_sentences(text)
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 15 or len(sentence) > 300:
                continue

            sent_words = _extract_keywords(sentence)
            if not sent_words:
                continue

            # Keyword overlap score
            overlap = goal_words & sent_words
            if not overlap:
                continue

            score = len(overlap) / len(goal_words)

            # Bonus for longer overlap
            if len(overlap) >= 3:
                score += 0.2
            if len(overlap) >= 5:
                score += 0.3

            # Penalise very short sentences
            if len(sent_words) < 4:
                score *= 0.6

            tab_short = tab_title[:50] if tab_title else "Untitled Tab"
            fact = f"From [{tab_short}]: {sentence}"

            scored.append((score, fact))

    # Sort by score descending, take top N
    scored.sort(key=lambda x: x[0], reverse=True)

    results: list[str] = []
    seen_sentences: set[str] = set()

    for _score, fact in scored:
        # Deduplicate near-identical sentences
        normalised = fact.lower().strip()
        if normalised in seen_sentences:
            continue
        seen_sentences.add(normalised)
        results.append(fact)
        if len(results) >= max_facts:
            break

    return results


# ── Tab Summary ──────────────────────────────────────────────────────


def summarize_tabs(
    tabs: list[dict],
    active_tab_id: Optional[int] = None,
    max_tabs: int = 10,
) -> str:
    """Produce a compact multi-line summary of open tabs.

    Args:
        tabs:          List of tab dicts.
        active_tab_id: The tabId of the active tab (for [ACTIVE] label).
        max_tabs:      Max tabs to include.

    Returns:
        Multi-line string like:
          [ACTIVE] Tab 1: "Quiz - Chapter 5" — quiz page — 8 questions detected
          [OPEN]   Tab 2: "Chapter 5 Notes" — article — reading content
    """
    lines: list[str] = []

    for i, tab in enumerate(tabs[:max_tabs]):
        tab_id = tab.get("tabId", tab.get("tab_id", 0))
        title = (tab.get("title") or "Untitled")[:60]
        page_type = tab.get("page_type", "general")
        q_count = tab.get("question_count", 0)
        elem_count = tab.get("interactive_element_count", 0)
        url = tab.get("url", "")

        # Domain extraction
        domain = ""
        if url:
            try:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc
            except Exception:
                domain = url[:40]

        label = "[ACTIVE]" if tab_id == active_tab_id else "[OPEN]  "

        info_parts: list[str] = [page_type]
        if q_count > 0:
            info_parts.append(f"{q_count} questions detected")
        if elem_count > 0:
            info_parts.append(f"{elem_count} interactive elements")
        if domain:
            info_parts.append(domain)

        info_str = " — ".join(info_parts)
        lines.append(f'{label} Tab {i + 1}: "{title}" — {info_str}')

    if len(tabs) > max_tabs:
        lines.append(f"... and {len(tabs) - max_tabs} more tab(s)")

    return "\n".join(lines)


# ── Planner Formatting ───────────────────────────────────────────────


def format_for_planner(
    context: MultiTabContext,
    max_chars: int = 3200,
) -> str:
    """Format a MultiTabContext into a string for the Claude planner prompt.

    Stays under ~800 tokens (≈ max_chars characters).

    Args:
        context:   The built MultiTabContext.
        max_chars: Maximum total character length.

    Returns:
        Formatted string suitable for prompt injection.
    """
    parts: list[str] = []

    # Tab overview
    if context.context_summary:
        parts.append("## Open Browser Tabs")
        summary = context.context_summary
        if len(summary) > max_chars // 2:
            summary = summary[: max_chars // 2] + "\n..."
        parts.append(summary)

    # Cross-tab facts
    if context.cross_tab_facts:
        parts.append("")
        parts.append("## Relevant Information from Other Tabs")
        for fact in context.cross_tab_facts[:8]:
            truncated = fact[:200]
            parts.append(f"  • {truncated}")

    # Active tab detail
    active = context.active_tab
    if active:
        parts.append("")
        parts.append("## Active Tab Details")
        parts.append(f"Title: {active.get('title', 'Unknown')}")
        parts.append(f"URL: {active.get('url', '')}")
        parts.append(f"Page Type: {active.get('page_type', 'general')}")

        q_count = active.get("question_count", 0)
        if q_count > 0:
            parts.append(f"Questions Detected: {q_count}")

    result = "\n".join(parts)

    # Truncate to max_chars if needed
    if len(result) > max_chars:
        result = result[:max_chars - 3] + "..."

    return result


# ── Text Utilities ───────────────────────────────────────────────────


def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords from text (lowercase, no stop words)."""
    text = text.lower()
    text = text.translate(str.maketrans("", "", string.punctuation))
    words = text.split()
    return {w for w in words if w and w not in _STOP_WORDS and len(w) > 1}


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences using simple regex."""
    # Split on period, question mark, exclamation mark, or newline
    sentences = re.split(r'(?<=[.!?])\s+|\n+', text)
    return [s.strip() for s in sentences if s.strip()]
