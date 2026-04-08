"""
BrowserAgent — Run Summarizer (Phase 10)

Analyzes and summarizes completed agent runs into compact RunSummary
objects.  Used by the learning engine, episodic store, and UI.

No external dependencies — standard library + Pydantic only.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger("browseragent.core.run_summarizer")


# ── Model ─────────────────────────────────────────────────────────────


class RunSummary(BaseModel):
    """Compact summary of a completed agent run."""

    run_id: str = Field(..., description="Unique run identifier.")
    goal: str = Field(..., description="The task goal.")
    domain: str = Field(default="", description="Website domain.")
    total_steps: int = Field(default=0, description="Total steps in the plan.")
    successful_steps: int = Field(default=0, description="Steps that succeeded.")
    failed_steps: int = Field(default=0, description="Steps that failed.")
    avg_confidence: float = Field(default=0.0, description="Average confidence across steps.")
    duration_ms: int = Field(default=0, description="Total run duration in milliseconds.")
    selectors_used: list[str] = Field(default_factory=list, description="Unique CSS selectors from successful steps.")
    buttons_clicked: list[str] = Field(default_factory=list, description="Button texts from successful CLICK actions.")
    questions_answered: int = Field(default=0, description="Number of MCQ questions answered.")
    final_status: str = Field(
        default="failed",
        description='Overall status: "success", "partial", "failed", or "stopped".',
    )
    key_findings: list[str] = Field(
        default_factory=list,
        description="Notable observations from the run.",
    )


# ── Main Summarizer ──────────────────────────────────────────────────


def summarize_run(
    run_id: str,
    goal: str,
    domain: str,
    steps: list[dict[str, Any]],
    results: list[dict[str, Any]],
    start_time_ms: int,
    end_time_ms: int,
) -> RunSummary:
    """Compute all fields of a RunSummary from steps and results.

    Args:
        run_id:        Unique identifier for this run.
        goal:          The task goal.
        domain:        The website domain.
        steps:         List of step dicts from the plan.
        results:       List of result dicts from execution.
        start_time_ms: Unix timestamp (ms) when the run started.
        end_time_ms:   Unix timestamp (ms) when the run ended.

    Returns:
        Populated RunSummary.
    """
    total = len(results)
    successes = sum(1 for r in results if r.get("success", False))
    failures = total - successes
    duration = max(0, end_time_ms - start_time_ms)

    # Status determination
    if total == 0:
        final_status = "stopped"
    else:
        rate = successes / total
        if rate > 0.8:
            final_status = "success"
        elif rate >= 0.4:
            final_status = "partial"
        else:
            # Check if manually stopped
            last_result = results[-1] if results else {}
            if last_result.get("error", "").lower().find("cancel") >= 0:
                final_status = "stopped"
            else:
                final_status = "failed"

    # Confidence
    confidences = [r.get("confidence", 0.5) for r in results if "confidence" in r]
    avg_conf = round(sum(confidences) / len(confidences), 4) if confidences else 0.0

    # Unique successful selectors
    selectors_used: list[str] = []
    for i, step in enumerate(steps):
        sel = step.get("target_selector") or step.get("selector") or ""
        if not sel:
            continue
        result = results[i] if i < len(results) else {}
        if result.get("success", False) and sel not in selectors_used:
            selectors_used.append(sel)

    # Button texts from successful CLICK actions
    buttons_clicked: list[str] = []
    for i, step in enumerate(steps):
        action = (step.get("action_type") or step.get("action") or "").upper()
        if action != "CLICK":
            continue
        result = results[i] if i < len(results) else {}
        if not result.get("success", False):
            continue
        text = (
            step.get("target_text") or step.get("text") or ""
        ).strip()
        if text and text not in buttons_clicked:
            buttons_clicked.append(text)

    # Questions answered (SELECT/CLICK on options with quiz-related reason)
    questions_answered = 0
    _Q_KEYWORDS = {"answer", "question", "option", "mcq", "quiz", "choice", "select"}
    for i, step in enumerate(steps):
        action = (step.get("action_type") or "").upper()
        if action not in ("SELECT", "CLICK"):
            continue
        result = results[i] if i < len(results) else {}
        if not result.get("success", False):
            continue
        reason = (step.get("reason") or "").lower()
        if any(kw in reason for kw in _Q_KEYWORDS):
            questions_answered += 1

    # Key findings
    findings = _extract_findings(steps, results, buttons_clicked, selectors_used, questions_answered)

    return RunSummary(
        run_id=run_id,
        goal=goal,
        domain=domain,
        total_steps=total,
        successful_steps=successes,
        failed_steps=failures,
        avg_confidence=avg_conf,
        duration_ms=duration,
        selectors_used=selectors_used,
        buttons_clicked=buttons_clicked,
        questions_answered=questions_answered,
        final_status=final_status,
        key_findings=findings,
    )


# ── Key Findings Extraction ──────────────────────────────────────────


def _extract_findings(
    steps: list[dict],
    results: list[dict],
    buttons: list[str],
    selectors: list[str],
    q_count: int,
) -> list[str]:
    """Extract notable observations about the run."""
    findings: list[str] = []

    # Question count
    if q_count > 0:
        findings.append(f"Answered {q_count} quiz question(s)")

    # Button patterns
    if buttons:
        findings.append(f"Clicked button(s): {', '.join(repr(b) for b in buttons[:5])}")

    # Selector diversity
    if len(selectors) > 3:
        findings.append(f"Used {len(selectors)} distinct CSS selectors")

    # Action type distribution
    action_counts: Counter[str] = Counter()
    for step in steps:
        action = (step.get("action_type") or step.get("action") or "UNKNOWN").upper()
        action_counts[action] += 1
    top_actions = action_counts.most_common(3)
    if top_actions:
        parts = [f"{a}: {c}" for a, c in top_actions]
        findings.append(f"Action breakdown — {', '.join(parts)}")

    # Errors
    errors: list[str] = []
    for r in results:
        err = r.get("error") or ""
        if err and err not in errors:
            errors.append(err)
    if errors:
        findings.append(f"Encountered {len(errors)} unique error(s): {errors[0][:80]}")

    # Navigation
    nav_count = sum(
        1 for s in steps
        if (s.get("action_type") or "").upper() in ("NAVIGATE", "SWITCH_TAB")
    )
    if nav_count > 0:
        findings.append(f"Navigated or switched tabs {nav_count} time(s)")

    return findings[:8]  # Cap at 8 findings


# ── Formatting ────────────────────────────────────────────────────────


def format_for_memory(summary: RunSummary) -> str:
    """Compact text representation for the episodic store.

    Format: Goal: ... | Domain: ... | Steps: X/Y | Status: ... | Findings: ...
    Max 500 chars.
    """
    findings_str = "; ".join(summary.key_findings) if summary.key_findings else "none"
    text = (
        f"Goal: {summary.goal} | "
        f"Domain: {summary.domain} | "
        f"Steps: {summary.successful_steps}/{summary.total_steps} | "
        f"Status: {summary.final_status} | "
        f"Findings: {findings_str}"
    )
    return text[:500]


# ── Run Comparison ────────────────────────────────────────────────────


def compare_runs(run1: RunSummary, run2: RunSummary) -> dict[str, Any]:
    """Compare two runs on the same domain for trend analysis.

    Args:
        run1: The earlier run.
        run2: The later run.

    Returns:
        Dict with improved_metrics, degraded_metrics,
        unchanged_metrics, and overall_trend.
    """
    improved: list[str] = []
    degraded: list[str] = []
    unchanged: list[str] = []

    # Success rate
    rate1 = run1.successful_steps / max(run1.total_steps, 1)
    rate2 = run2.successful_steps / max(run2.total_steps, 1)
    _classify("success_rate", rate1, rate2, improved, degraded, unchanged, threshold=0.05)

    # Confidence
    _classify("avg_confidence", run1.avg_confidence, run2.avg_confidence,
              improved, degraded, unchanged, threshold=0.05)

    # Efficiency (fewer steps = better, so invert)
    if run1.total_steps > 0 and run2.total_steps > 0:
        if run2.total_steps < run1.total_steps - 1:
            improved.append("total_steps (more efficient)")
        elif run2.total_steps > run1.total_steps + 1:
            degraded.append("total_steps (less efficient)")
        else:
            unchanged.append("total_steps")
    else:
        unchanged.append("total_steps")

    # Questions answered (more = better)
    _classify("questions_answered", run1.questions_answered, run2.questions_answered,
              improved, degraded, unchanged, threshold=0.5)

    # Overall trend
    if len(improved) > len(degraded):
        trend = "improving"
    elif len(degraded) > len(improved):
        trend = "degrading"
    else:
        trend = "stable"

    return {
        "improved_metrics": improved,
        "degraded_metrics": degraded,
        "unchanged_metrics": unchanged,
        "overall_trend": trend,
    }


def _classify(
    name: str,
    val1: float,
    val2: float,
    improved: list[str],
    degraded: list[str],
    unchanged: list[str],
    threshold: float = 0.05,
) -> None:
    """Classify a metric change as improved, degraded, or unchanged."""
    diff = val2 - val1
    if diff > threshold:
        improved.append(name)
    elif diff < -threshold:
        degraded.append(name)
    else:
        unchanged.append(name)


# ── Domain Stats ──────────────────────────────────────────────────────


def get_domain_stats(domain: str, db_path: str) -> dict[str, Any]:
    """Query task_runs for aggregate statistics on a domain.

    Args:
        domain:  The website domain.
        db_path: Path to the SQLite database.

    Returns:
        Dict with total_runs, success_rate, avg_confidence,
        most_common_goals, first_run, last_run.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM task_runs WHERE domain = ? ORDER BY created_at ASC",
            (domain,),
        ).fetchall()

        if not rows:
            return {
                "total_runs": 0,
                "success_rate": 0.0,
                "avg_confidence": 0.0,
                "most_common_goals": [],
                "first_run": None,
                "last_run": None,
            }

        total = len(rows)
        successes = sum(1 for r in rows if r["success"])
        success_rate = round(successes / total, 4) if total > 0 else 0.0

        # Parse results for confidence
        all_confidences: list[float] = []
        goal_counter: Counter[str] = Counter()

        for row in rows:
            row_dict = dict(row)
            goal_counter[row_dict["goal"]] += 1
            try:
                results_json = row_dict.get("results_json", "[]") or "[]"
                results = json.loads(results_json)
                for r in results:
                    if isinstance(r, dict) and "confidence" in r:
                        all_confidences.append(r["confidence"])
            except (json.JSONDecodeError, TypeError):
                pass

        avg_conf = round(
            sum(all_confidences) / len(all_confidences), 4
        ) if all_confidences else 0.0

        most_common = [g for g, _ in goal_counter.most_common(5)]

        first_row = dict(rows[0]) if rows else {}
        last_row = dict(rows[-1]) if rows else {}
        first_run = first_row.get("created_at")
        last_run = last_row.get("created_at")

        return {
            "total_runs": total,
            "success_rate": success_rate,
            "avg_confidence": avg_conf,
            "most_common_goals": most_common,
            "first_run": first_run,
            "last_run": last_run,
        }
    finally:
        conn.close()
