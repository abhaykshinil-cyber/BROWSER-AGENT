"""
BrowserAgent — Post-Run Critic (Phase 5)

Analyses completed runs to extract actionable insights:
  • Overall success / failure determination
  • Failed step diagnosis
  • Common failure pattern identification across multiple runs
  • Suggested memory updates (new rules to create)

This module runs after every task and feeds improvements back into
the memory system so the agent gets smarter over time.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

from schemas import ActionResult, ActionStep, ActionType, MemoryItem, MemoryType

logger = logging.getLogger("browseragent.critic")

# ── Single-Run Analysis ──────────────────────────────────────────────


def analyze_run(
    steps: list[ActionStep],
    results: list[ActionResult],
) -> dict[str, Any]:
    """Analyse a completed run and return a structured report.

    Returns:
        {
            overall_success: bool,
            total_steps: int,
            succeeded: int,
            failed: int,
            success_rate: float,
            failed_steps: [{ step_id, action_type, reason, error }],
            suggestions: [str],
        }
    """
    total = len(results)
    succeeded = sum(1 for r in results if r.success)
    failed_count = total - succeeded

    # ── Build failed-step details ─────────────────────────────────
    result_map: dict[str, ActionResult] = {r.step_id: r for r in results}
    failed_steps: list[dict[str, Any]] = []

    for step in steps:
        result = result_map.get(step.step_id)
        if result and not result.success:
            action_type = step.action_type.value if isinstance(step.action_type, ActionType) else step.action_type
            failed_steps.append({
                "step_id":     step.step_id,
                "action_type": action_type,
                "reason":      step.reason,
                "target":      step.target_selector or step.target_text or "",
                "error":       result.error or "Unknown error",
            })

    # ── Generate suggestions ──────────────────────────────────────
    suggestions: list[str] = []

    for fs in failed_steps:
        action = fs["action_type"]
        error = fs.get("error", "")

        if "not found" in error.lower() or "element not found" in error.lower():
            suggestions.append(
                f"Step [{action}] failed because the target element was not found. "
                f"The selector '{fs['target']}' may be stale or the page layout changed. "
                f"Consider using target_text as a fallback."
            )

        if "timeout" in error.lower():
            suggestions.append(
                f"Step [{action}] timed out. The page may be slow to load. "
                f"Add a WAIT step before this action."
            )

        if action == "TYPE" and "focus" in error.lower():
            suggestions.append(
                f"TYPE step failed to focus the input. Try adding a CLICK "
                f"step on the input field before typing."
            )

        if action == "CLICK" and "disabled" in error.lower():
            suggestions.append(
                f"CLICK target was disabled. A prerequisite form field may "
                f"need to be filled first."
            )

    # General suggestions based on patterns
    if failed_count > 0 and succeeded == 0:
        suggestions.append(
            "All steps failed. The page structure may have changed significantly. "
            "Consider rescanning and replanning."
        )
    elif failed_count > total * 0.5:
        suggestions.append(
            f"{failed_count} of {total} steps failed. The plan may need to be "
            f"regenerated with a fresh page scan."
        )

    # Success heuristic: all steps succeeded, OR >80% succeeded and
    # the last step succeeded (the goal was likely achieved)
    last_result = results[-1] if results else None
    overall_success = (
        (succeeded == total)
        or (total > 0 and succeeded / total >= 0.8 and last_result and last_result.success)
    )

    return {
        "overall_success": overall_success,
        "total_steps":     total,
        "succeeded":       succeeded,
        "failed":          failed_count,
        "success_rate":    round(succeeded / total, 3) if total > 0 else 0.0,
        "failed_steps":    failed_steps,
        "suggestions":     suggestions,
    }


# ── Cross-Run Pattern Identification ─────────────────────────────────


def identify_patterns(runs: list[dict[str, Any]]) -> list[str]:
    """Analyse multiple historical runs to find common failure patterns.

    Looks for:
      • Frequently failing action types
      • Frequently failing selectors
      • Domains with low success rates
      • Common error messages

    Args:
        runs: List of run dicts, each with keys:
              goal, domain, steps (list), results (list), success (bool)

    Returns:
        Human-readable pattern descriptions.
    """
    if not runs:
        return []

    patterns: list[str] = []

    # Counters
    action_failures: Counter = Counter()
    selector_failures: Counter = Counter()
    domain_stats: dict[str, dict[str, int]] = {}
    error_fragments: Counter = Counter()

    for run in runs:
        domain = run.get("domain", "unknown")
        success = run.get("success", False)

        if domain not in domain_stats:
            domain_stats[domain] = {"total": 0, "failures": 0}
        domain_stats[domain]["total"] += 1
        if not success:
            domain_stats[domain]["failures"] += 1

        results = run.get("results", [])
        steps = run.get("steps", [])

        for i, result in enumerate(results):
            if isinstance(result, dict) and not result.get("success", True):
                # Count action type failures
                step = steps[i] if i < len(steps) else {}
                action_type = step.get("action_type", "unknown")
                action_failures[action_type] += 1

                # Count selector failures
                selector = step.get("target_selector", "")
                if selector:
                    selector_failures[selector] += 1

                # Count error fragments
                error = result.get("error", "")
                if error:
                    # Normalise: extract key phrases
                    for phrase in _extract_error_phrases(error):
                        error_fragments[phrase] += 1

    # ── Build patterns ────────────────────────────────────────────

    # Frequently failing action types
    for action_type, count in action_failures.most_common(3):
        if count >= 2:
            patterns.append(
                f"Action [{action_type}] failed {count} times across runs. "
                f"Consider alternative approaches for this action."
            )

    # Domains with poor success rates
    for domain, stats in domain_stats.items():
        total = stats["total"]
        failures = stats["failures"]
        if total >= 2 and failures / total >= 0.5:
            rate = round((1 - failures / total) * 100)
            patterns.append(
                f"Domain '{domain}' has a {rate}% success rate "
                f"({failures} failures in {total} runs). "
                f"Site-specific rules may be needed."
            )

    # Frequently failing selectors
    for selector, count in selector_failures.most_common(3):
        if count >= 2:
            patterns.append(
                f"Selector '{selector[:60]}' failed {count} times. "
                f"It may be dynamic. Use text-based targeting instead."
            )

    # Common errors
    for phrase, count in error_fragments.most_common(3):
        if count >= 3:
            patterns.append(
                f"Error '{phrase}' appeared {count} times. "
                f"This indicates a systemic issue."
            )

    if not patterns:
        total_runs = len(runs)
        total_failures = sum(1 for r in runs if not r.get("success"))
        if total_failures == 0:
            patterns.append(
                f"All {total_runs} analysed runs succeeded. No failure patterns detected."
            )
        else:
            patterns.append(
                f"Analysed {total_runs} runs ({total_failures} failures) "
                f"but no strong recurring pattern was found."
            )

    logger.info("Identified %d patterns from %d runs", len(patterns), len(runs))
    return patterns


# ── Memory Update Suggestions ────────────────────────────────────────


def suggest_memory_updates(
    run: dict[str, Any],
    results: list[dict[str, Any]],
) -> list[MemoryItem]:
    """Generate MemoryItem suggestions based on a completed run.

    Creates rules for:
      • Domain-specific button patterns (if next/submit worked)
      • Selector reliability warnings (if selectors failed)
      • Successful sequences worth remembering

    Args:
        run: { task_id, goal, domain, steps, results, success }
        results: List of ActionResult dicts.

    Returns:
        List of MemoryItem suggestions to persist.
    """
    suggestions: list[MemoryItem] = []
    now = datetime.now(timezone.utc)

    domain = run.get("domain")
    goal = run.get("goal", "")
    success = run.get("success", False)
    steps = run.get("steps", [])

    # ── 1. Selector failure warning ───────────────────────────────
    for i, result in enumerate(results):
        if isinstance(result, dict) and not result.get("success", True):
            error = result.get("error", "")
            step = steps[i] if i < len(steps) else {}
            selector = step.get("target_selector", "")

            if selector and ("not found" in error.lower() or "element not found" in error.lower()):
                suggestions.append(MemoryItem(
                    memory_id=uuid.uuid4().hex,
                    type=MemoryType.SITE if domain else MemoryType.SEMANTIC,
                    scope=domain or "global",
                    domain=domain,
                    instruction=(
                        f"Selector '{selector[:80]}' is unreliable on "
                        f"{domain or 'this site'}. Use target_text "
                        f"'{step.get('target_text', '')}' as fallback."
                    ),
                    trigger_conditions=[
                        f"targeting element with selector '{selector[:60]}'",
                    ],
                    preferred_actions=["Use text-based targeting"],
                    avoid_actions=[f"Rely on selector '{selector[:60]}'"],
                    confidence=0.7,
                    created_at=now,
                    updated_at=now,
                ))

    # ── 2. Successful sequence rule ───────────────────────────────
    if success and len(steps) >= 2:
        # Summarise the successful approach
        step_summary = ", ".join(
            f"{s.get('action_type', '?')} on '{s.get('target_text', s.get('target_selector', '?'))[:30]}'"
            for s in steps[:5]
        )
        suggestions.append(MemoryItem(
            memory_id=uuid.uuid4().hex,
            type=MemoryType.EPISODIC,
            scope=domain or "global",
            domain=domain,
            instruction=(
                f"Successfully completed '{goal[:80]}' using sequence: "
                f"{step_summary}"
            ),
            trigger_conditions=[f"goal is similar to '{goal[:60]}'"],
            preferred_actions=[step_summary],
            avoid_actions=[],
            confidence=0.8,
            created_at=now,
            updated_at=now,
        ))

    # ── 3. Total failure warning ──────────────────────────────────
    failed_count = sum(1 for r in results if isinstance(r, dict) and not r.get("success", True))
    if failed_count == len(results) and len(results) > 0:
        suggestions.append(MemoryItem(
            memory_id=uuid.uuid4().hex,
            type=MemoryType.SITE if domain else MemoryType.SEMANTIC,
            scope=domain or "global",
            domain=domain,
            instruction=(
                f"Task '{goal[:60]}' failed completely on {domain or 'this page'}. "
                f"The page structure may require a different approach."
            ),
            trigger_conditions=[f"attempting '{goal[:60]}' on {domain or 'this page'}"],
            preferred_actions=["Rescan page before planning", "Use text-based targeting"],
            avoid_actions=["Reuse the same plan without rescanning"],
            confidence=0.6,
            created_at=now,
            updated_at=now,
        ))

    # ── 4. Domain-specific button discovery ───────────────────────
    if domain and success:
        submit_patterns: list[str] = []
        next_patterns: list[str] = []

        for i, step in enumerate(steps):
            action = step.get("action_type", "")
            text = step.get("target_text", "")
            result = results[i] if i < len(results) else {}

            if isinstance(result, dict) and result.get("success") and text:
                if action in ("SUBMIT",):
                    submit_patterns.append(text)
                elif action == "CLICK":
                    lower = text.lower()
                    if any(kw in lower for kw in ("next", "continue", "proceed", "forward")):
                        next_patterns.append(text)

        if submit_patterns or next_patterns:
            parts: list[str] = []
            if next_patterns:
                parts.append(f"Next buttons: {', '.join(next_patterns[:3])}")
            if submit_patterns:
                parts.append(f"Submit buttons: {', '.join(submit_patterns[:3])}")

            suggestions.append(MemoryItem(
                memory_id=uuid.uuid4().hex,
                type=MemoryType.SITE,
                scope=domain,
                domain=domain,
                instruction=f"On {domain}: {'; '.join(parts)}",
                trigger_conditions=[f"on {domain}"],
                preferred_actions=parts,
                avoid_actions=[],
                confidence=0.9,
                created_at=now,
                updated_at=now,
            ))

    logger.info(
        "Suggested %d memory updates for run %s",
        len(suggestions), run.get("task_id", "?"),
    )
    return suggestions


# ── Helpers ───────────────────────────────────────────────────────────

def _extract_error_phrases(error: str) -> list[str]:
    """Pull key phrases from an error message for pattern counting."""
    error = error.lower().strip()
    phrases: list[str] = []

    known_patterns = [
        "element not found",
        "timeout",
        "not clickable",
        "disabled",
        "not visible",
        "no response",
        "selector invalid",
        "permission denied",
        "network error",
    ]

    for pattern in known_patterns:
        if pattern in error:
            phrases.append(pattern)

    if not phrases:
        # Take the first 40 chars as a fallback identifier
        phrases.append(error[:40])

    return phrases
