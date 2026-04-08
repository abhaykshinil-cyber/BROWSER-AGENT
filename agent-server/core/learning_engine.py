"""
BrowserAgent — Learning Engine (Phase 10)

Post-run learning engine that analyzes completed agent runs to:
  1. Discover which CSS selectors and button patterns worked
  2. Update site profiles with successful patterns
  3. Generate new memory rules via Claude for reusable knowledge
  4. Adjust confidence of rules that were applied during the run

Called by the agent loop after every completed run (success or failure).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from core.llm import call_gemini, GEMINI_MAIN

from schemas import MemoryItem, MemoryType
from memory.db import MemoryDB
from memory.semantic_store import SemanticStore
from memory.site_profiles import (
    SiteProfile,
    get_profile,
    save_profile,
    update_from_run as sp_update_from_run,
)

logger = logging.getLogger("browseragent.core.learning")

# ── Prompt paths ──────────────────────────────────────────────────────

_PROMPT_DIR = "prompts"
_SITE_ADAPT_PROMPT = "site_adaptation.txt"


def _load_prompt(filename: str) -> str:
    """Load a prompt template file."""
    import os
    path = os.path.join(os.path.dirname(__file__), "..", _PROMPT_DIR, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.warning("Prompt file not found: %s", path)
        return ""


# ── Button-pattern keywords ──────────────────────────────────────────

_NEXT_WORDS = frozenset({
    "next", "continue", "proceed", "forward", "go", "siguiente",
})
_SUBMIT_WORDS = frozenset({
    "submit", "finish", "done", "complete", "send", "grade",
    "check", "save", "confirm", "enviar",
})


# ── Main Entry Point ─────────────────────────────────────────────────


def learn_from_run(
    run_id: str,
    goal: str,
    domain: str,
    steps: list[dict[str, Any]],
    results: list[dict[str, Any]],
    db_path: str,
    api_key: str,
    model: str = GEMINI_MAIN,
) -> list[str]:
    """Analyze a completed run and generate learning artifacts.

    This is the main entry point called after every completed run.

    Args:
        run_id:  Unique identifier for this run.
        goal:    The task goal.
        domain:  The website domain.
        steps:   List of step dicts from the plan.
        results: List of result dicts from execution.
        db_path: Path to the SQLite database.
        api_key: Google Gemini API key for rule generation.
        model:   Gemini model to use.

    Returns:
        List of memory_ids for new or updated rules.
    """
    memory_ids: list[str] = []

    # 1. Analyze patterns
    selector_patterns = analyze_selector_patterns(steps, results)
    button_patterns = analyze_button_patterns(steps, results)

    logger.info(
        "Run %s analysis: %d successful selectors, %d next patterns, %d submit patterns",
        run_id,
        len(selector_patterns.get("successful_selectors", [])),
        len(button_patterns.get("next_patterns", [])),
        len(button_patterns.get("submit_patterns", [])),
    )

    # 2. Update site profile
    update_site_profile_from_run(domain, steps, results, db_path)

    # 3. Determine overall success
    success_count = sum(
        1 for r in results if r.get("success", False)
    )
    total = len(results) if results else 1
    overall_success = success_count / total > 0.5

    # 4. Generate new rules if run was successful and found patterns
    has_new_patterns = (
        len(selector_patterns.get("successful_selectors", [])) > 0
        or len(button_patterns.get("next_patterns", [])) > 0
        or len(button_patterns.get("submit_patterns", [])) > 0
    )

    if overall_success and has_new_patterns and api_key:
        try:
            combined_patterns = {
                **selector_patterns,
                **button_patterns,
            }
            new_rules = generate_memory_rules(
                domain=domain,
                patterns=combined_patterns,
                goal=goal,
                api_key=api_key,
                model=model,
            )

            # Save new rules
            db = MemoryDB(db_path)
            db.init()
            store = SemanticStore(db)
            try:
                for rule in new_rules:
                    mid = store.save_rule(rule)
                    memory_ids.append(mid)
                    logger.info("Saved learned rule: %s — %s", mid, rule.instruction[:60])
            finally:
                db.close()

        except Exception as e:
            logger.error("Rule generation failed: %s", e, exc_info=True)

    # 5. Adjust confidence of rules used during the run
    memories_used = _extract_memories_used(steps)
    if memories_used:
        adjust_memory_confidence(steps, results, memories_used, db_path)
        memory_ids.extend(memories_used)

    return memory_ids


# ── Pattern Analysis ──────────────────────────────────────────────────


def analyze_selector_patterns(
    steps: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Find which CSS selectors were used in successful vs failed steps.

    Args:
        steps:   List of step dicts (must contain target_selector).
        results: Corresponding result dicts (must contain success).

    Returns:
        Dict with "successful_selectors" and "failed_selectors" lists.
    """
    successful: list[str] = []
    failed: list[str] = []

    for i, step in enumerate(steps):
        selector = step.get("target_selector") or step.get("selector") or ""
        if not selector:
            continue

        result = results[i] if i < len(results) else {}
        if result.get("success", False):
            if selector not in successful:
                successful.append(selector)
        else:
            if selector not in failed:
                failed.append(selector)

    return {
        "successful_selectors": successful,
        "failed_selectors": failed,
    }


def analyze_button_patterns(
    steps: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Find which button texts were clicked and categorize them.

    Args:
        steps:   List of step dicts.
        results: Corresponding result dicts.

    Returns:
        Dict with "next_patterns" and "submit_patterns" lists.
    """
    next_patterns: list[str] = []
    submit_patterns: list[str] = []

    for i, step in enumerate(steps):
        action = (step.get("action_type") or step.get("action") or "").upper()
        if action != "CLICK":
            continue

        result = results[i] if i < len(results) else {}
        if not result.get("success", False):
            continue

        # Get button text
        text = (
            step.get("target_text")
            or step.get("text")
            or step.get("input_value")
            or ""
        ).strip()
        if not text:
            continue

        lower = text.lower()
        words = set(lower.split())

        if words & _NEXT_WORDS and text not in next_patterns:
            next_patterns.append(text)
        elif words & _SUBMIT_WORDS and text not in submit_patterns:
            submit_patterns.append(text)

    return {
        "next_patterns": next_patterns,
        "submit_patterns": submit_patterns,
    }


# ── Rule Generation ──────────────────────────────────────────────────


def generate_memory_rules(
    domain: str,
    patterns: dict[str, Any],
    goal: str,
    api_key: str,
    model: str = GEMINI_MAIN,
) -> list[MemoryItem]:
    """Call Gemini to generate reusable rules from discovered patterns.

    Args:
        domain:  The website domain.
        patterns: Dict of discovered selectors and button texts.
        goal:    The original task goal.
        api_key: Google Gemini API key.
        model:   Gemini model identifier.

    Returns:
        List of MemoryItem objects ready to be saved.
        Rules with confidence < 0.6 are filtered out.
    """
    system_prompt = _load_prompt(_SITE_ADAPT_PROMPT)
    if not system_prompt:
        logger.warning("No site adaptation prompt found, skipping rule generation")
        return []

    user_message = json.dumps({
        "domain": domain,
        "goal": goal,
        "patterns_discovered": patterns,
        "current_profile": None,
    }, indent=2)

    try:
        raw_text = call_gemini(
            api_key=api_key,
            model_name=model,
            system_prompt=system_prompt,
            user_content=user_message,
            max_tokens=2048,
        )
        # Extract JSON from response (handle markdown code fences)
        json_match = re.search(r'\{[\s\S]*\}', raw_text)
        if not json_match:
            logger.warning("Could not extract JSON from Gemini response")
            return []

        parsed = json.loads(json_match.group())

    except json.JSONDecodeError as e:
        logger.error("Failed to parse Gemini response as JSON: %s", e)
        return []
    except Exception as e:
        logger.error("Gemini API call failed: %s", e, exc_info=True)
        return []

    # Convert to MemoryItem objects
    rules: list[MemoryItem] = []
    for rule_dict in parsed.get("new_memory_rules", []):
        confidence = rule_dict.get("confidence", 0.5)
        if confidence < 0.6:
            continue

        instruction = rule_dict.get("instruction", "").strip()
        if not instruction or len(instruction) < 10:
            continue

        # Skip overly generic rules
        generic_phrases = [
            "click buttons", "fill forms", "select options",
            "interact with elements", "submit data",
        ]
        if any(gp in instruction.lower() for gp in generic_phrases):
            continue

        item = MemoryItem(
            memory_id=uuid.uuid4().hex,
            type=MemoryType(rule_dict.get("type", "site")),
            scope=rule_dict.get("scope", "domain"),
            domain=domain,
            instruction=instruction,
            trigger_conditions=rule_dict.get("trigger_conditions", []),
            preferred_actions=rule_dict.get("preferred_actions", []),
            avoid_actions=rule_dict.get("avoid_actions", []),
            confidence=min(confidence, 1.0),
        )
        rules.append(item)

    logger.info("Generated %d rules for %s (filtered from %d candidates)",
                len(rules), domain,
                len(parsed.get("new_memory_rules", [])))
    return rules


# ── Site Profile Update ──────────────────────────────────────────────


def update_site_profile_from_run(
    domain: str,
    steps: list[dict[str, Any]],
    results: list[dict[str, Any]],
    db_path: str,
) -> None:
    """Extract successful patterns and update the site profile.

    Args:
        domain:  The website domain.
        steps:   List of step dicts.
        results: Corresponding result dicts.
        db_path: Path to the SQLite database.
    """
    selectors = analyze_selector_patterns(steps, results)
    buttons = analyze_button_patterns(steps, results)

    successful_selectors = selectors.get("successful_selectors", [])
    button_texts = []
    for step in steps:
        action = (step.get("action_type") or "").upper()
        if action == "CLICK":
            text = step.get("target_text") or step.get("text") or ""
            if text.strip():
                button_texts.append(text.strip())

    # Count questions answered
    questions_answered = sum(
        1 for s in steps
        if (s.get("action_type") or "").upper() in ("SELECT", "CLICK")
        and any(
            kw in (s.get("reason") or "").lower()
            for kw in ("answer", "question", "option", "mcq", "quiz")
        )
    )

    # Overall success
    success_count = sum(1 for r in results if r.get("success", False))
    total = len(results) if results else 1
    overall_success = success_count / total > 0.5

    # Average confidence from results
    confidences = [r.get("confidence", 0.5) for r in results if "confidence" in r]
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.5

    run_data = {
        "selectors_used": successful_selectors,
        "buttons_clicked": button_texts,
        "questions_answered": questions_answered,
        "success": overall_success,
        "confidence": avg_conf,
    }

    sp_update_from_run(domain, run_data, db_path)


# ── Confidence Adjustment ─────────────────────────────────────────────


def adjust_memory_confidence(
    steps: list[dict[str, Any]],
    results: list[dict[str, Any]],
    memories_used: list[str],
    db_path: str,
) -> None:
    """Adjust confidence of rules applied during this run.

    For each memory_id: if the step where it was used succeeded,
    record a positive outcome; otherwise record a negative one.

    Args:
        steps:        List of step dicts.
        results:      Corresponding result dicts.
        memories_used: List of memory_ids that were applied.
        db_path:      Path to the SQLite database.
    """
    db = MemoryDB(db_path)
    db.init()
    store = SemanticStore(db)

    try:
        # Build a map of memory_id → step indices where it was used
        memory_step_map: dict[str, list[int]] = {}
        for i, step in enumerate(steps):
            mid = step.get("memory_id") or step.get("source_memory_id") or ""
            if mid and mid in memories_used:
                memory_step_map.setdefault(mid, []).append(i)

        for mid in memories_used:
            step_indices = memory_step_map.get(mid, [])

            if not step_indices:
                # Memory was used but we can't track which step — use overall run success
                overall_success = sum(
                    1 for r in results if r.get("success", False)
                ) > len(results) / 2
                store.record_outcome(mid, overall_success)
                continue

            # Check success of the specific steps this memory influenced
            for idx in step_indices:
                result = results[idx] if idx < len(results) else {}
                success = result.get("success", False)
                store.record_outcome(mid, success)

        logger.info(
            "Adjusted confidence for %d memories", len(memories_used)
        )
    finally:
        db.close()


# ── Internal Helpers ──────────────────────────────────────────────────


def _extract_memories_used(steps: list[dict[str, Any]]) -> list[str]:
    """Extract unique memory_ids referenced by steps."""
    ids: list[str] = []
    for step in steps:
        mid = step.get("memory_id") or step.get("source_memory_id") or ""
        if mid and mid not in ids:
            ids.append(mid)
    return ids
