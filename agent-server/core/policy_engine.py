"""
BrowserAgent — Policy Engine (Phase 7)

Converts natural-language user teachings into structured MemoryItem
objects via Claude, validates them, applies stored rules to planned
action steps, and retrieves context-relevant rules.

This is a full rewrite of the Phase 3 policy_engine.py, adding:
  • parse_teaching_prompt()  — Claude-backed with fallback
  • validate_memory_item()   — structural validation with warnings
  • apply_rules_to_plan()    — rule enforcement on action plans
  • get_relevant_rules_for_context() — context-aware retrieval
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.llm import call_gemini, GEMINI_MAIN

from config import AgentConfig
from schemas import MemoryItem, MemoryType, TeachingPrompt

logger = logging.getLogger("browseragent.policy")

# ── Load system prompt ────────────────────────────────────────────────

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "teacher.txt"


def _load_system_prompt() -> str:
    """Load the teacher prompt from disk, with a hard-coded fallback."""
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Teacher prompt not found at %s — using fallback", _PROMPT_PATH)
        return (
            "You are a rule parser. Convert the user's teaching instruction "
            "into a JSON object with fields: type, scope, domain, instruction, "
            "trigger_conditions, preferred_actions, avoid_actions, confidence, notes. "
            "Return ONLY valid JSON."
        )


TEACHER_SYSTEM_PROMPT = _load_system_prompt()


# ── Parsing ───────────────────────────────────────────────────────────


async def parse_teaching_prompt(
    raw_text: str,
    api_key: str,
    model: str = GEMINI_MAIN,
    domain: Optional[str] = None,
    scope: Optional[str] = None,
) -> MemoryItem:
    """Parse a user's teaching instruction into a MemoryItem via Gemini.

    Falls back to rule-based extraction if:
      • No API key is provided
      • Gemini returns invalid JSON
      • The API call fails (rate limit, network, etc.)

    Args:
        raw_text: The user's raw natural-language teaching instruction.
        api_key:  Google Gemini API key.
        model:    Gemini model identifier.
        domain:   Optional domain context (e.g. "github.com").
        scope:    Optional scope override ("global", "domain", "page_pattern").

    Returns:
        A populated MemoryItem ready for persistence.

    Raises:
        ValueError: If Claude returns completely unparseable output AND
                    the fallback also fails (should not happen in practice).
    """
    if not api_key:
        logger.info("No API key — using rule-based teaching parser")
        return _fallback_parse(raw_text, domain, scope)

    user_content = f'Parse this teaching instruction into a structured memory rule:\n\n"{raw_text}"'
    if domain:
        user_content += f"\n\nThe user specified domain: {domain}"
    if scope and scope != "global":
        user_content += f"\nThe user specified scope: {scope}"

    try:
        raw_response = call_gemini(
            api_key=api_key,
            model_name=model,
            system_prompt=TEACHER_SYSTEM_PROMPT,
            user_content=user_content,
            max_tokens=1024,
        )
    except Exception as exc:
        logger.error("Gemini API error in policy engine: %s", exc)
        return _fallback_parse(raw_text, domain, scope)

    if not raw_response.strip():
        logger.warning("Empty response from Claude — using fallback")
        return _fallback_parse(raw_text, domain, scope)

    parsed = _parse_json(raw_response)
    if parsed is None:
        logger.warning("Could not parse Claude response, using fallback")
        return _fallback_parse(raw_text, domain, scope)

    return _build_memory_from_parsed(parsed, raw_text, domain, scope)


def _build_memory_from_parsed(
    parsed: dict[str, Any],
    raw_text: str,
    domain: Optional[str],
    scope: Optional[str],
) -> MemoryItem:
    """Convert Claude's JSON output into a validated MemoryItem."""
    now = datetime.now(timezone.utc)

    # Resolve memory type
    raw_type = parsed.get("type", "user_rule")
    try:
        mem_type = MemoryType(raw_type)
    except ValueError:
        mem_type = MemoryType.USER_RULE

    # Use parsed domain or fall back to provided domain
    resolved_domain = parsed.get("domain") or domain
    resolved_scope = parsed.get("scope", scope or "global")

    # Ensure trigger_conditions is a list of strings
    triggers = parsed.get("trigger_conditions", [])
    if isinstance(triggers, str):
        triggers = [triggers]
    triggers = [str(t) for t in triggers if t]

    # Ensure preferred/avoid actions are lists of strings
    preferred = parsed.get("preferred_actions", [])
    if isinstance(preferred, str):
        preferred = [preferred]
    preferred = [str(a) for a in preferred if a]

    avoid = parsed.get("avoid_actions", [])
    if isinstance(avoid, str):
        avoid = [avoid]
    avoid = [str(a) for a in avoid if a]

    confidence = parsed.get("confidence", 1.0)
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (ValueError, TypeError):
        confidence = 1.0

    return MemoryItem(
        memory_id=uuid.uuid4().hex,
        type=mem_type,
        scope=resolved_scope,
        domain=resolved_domain,
        instruction=parsed.get("instruction", raw_text),
        trigger_conditions=triggers,
        preferred_actions=preferred,
        avoid_actions=avoid,
        confidence=confidence,
        success_count=0,
        failure_count=0,
        created_at=now,
        updated_at=now,
    )


def _fallback_parse(
    raw_text: str,
    domain: Optional[str] = None,
    scope: Optional[str] = None,
) -> MemoryItem:
    """Simple rule-based parser when Claude is unavailable.

    Extracts domain from text if mentioned, and stores the raw
    instruction as-is with keyword-based preferred/avoid actions.
    """
    now = datetime.now(timezone.utc)

    # Detect domain from text if not provided
    if not domain:
        domain_match = re.search(
            r"(?:on|at|for)\s+([\w.-]+\.(?:com|org|net|io|dev|co|edu|gov|uk|ca))",
            raw_text,
            re.I,
        )
        if domain_match:
            domain = domain_match.group(1).lower()

    resolved_scope = domain if domain else (scope or "global")
    mem_type = MemoryType.SITE if domain else MemoryType.USER_RULE

    preferred: list[str] = []
    avoid: list[str] = []

    text_lower = raw_text.lower()
    if any(kw in text_lower for kw in ("always", "prefer", "use", "should", "must")):
        preferred.append(raw_text)
    if any(kw in text_lower for kw in ("never", "don't", "avoid", "do not", "shouldn't")):
        avoid.append(raw_text)
    if not preferred and not avoid:
        preferred.append(raw_text)

    # Extract trigger conditions from keywords
    triggers: list[str] = []
    if domain:
        triggers.append(f"on {domain}")
    if "quiz" in text_lower or "mcq" in text_lower:
        triggers.append("taking a quiz or MCQ")
    if "form" in text_lower:
        triggers.append("filling out a form")
    if "click" in text_lower or "button" in text_lower:
        triggers.append("interacting with buttons")
    if not triggers:
        triggers.append("general browsing")

    return MemoryItem(
        memory_id=uuid.uuid4().hex,
        type=mem_type,
        scope=resolved_scope,
        domain=domain,
        instruction=raw_text,
        trigger_conditions=triggers,
        preferred_actions=preferred,
        avoid_actions=avoid,
        confidence=1.0,
        success_count=0,
        failure_count=0,
        created_at=now,
        updated_at=now,
    )


# ── Validation ────────────────────────────────────────────────────────


def validate_memory_item(item: MemoryItem) -> dict[str, Any]:
    """Validate a MemoryItem for structural correctness.

    Returns:
        {
            "valid": bool,
            "warnings": list[str]
        }

    A MemoryItem is invalid if:
      • instruction is empty
      • confidence is outside 0.0-1.0
    Warnings are issued for:
      • No trigger_conditions
      • domain is None for a "site" type rule
      • No preferred_actions defined
      • Instruction is very short (< 10 chars)
    """
    warnings: list[str] = []
    valid = True

    # ── Hard failures ─────────────────────────────────────────────
    if not item.instruction or not item.instruction.strip():
        warnings.append("Instruction is empty")
        valid = False

    if item.confidence < 0.0 or item.confidence > 1.0:
        warnings.append(f"Confidence {item.confidence} is outside 0.0-1.0 range")
        valid = False

    # ── Soft warnings ─────────────────────────────────────────────
    if not item.trigger_conditions:
        warnings.append("No trigger_conditions defined — rule may never activate")

    mem_type = item.type.value if isinstance(item.type, MemoryType) else item.type
    if mem_type == "site" and not item.domain:
        warnings.append("Rule type is 'site' but no domain is specified")

    if not item.preferred_actions:
        warnings.append("No preferred_actions defined — rule has no actionable guidance")

    if item.instruction and len(item.instruction.strip()) < 10:
        warnings.append("Instruction is very short (< 10 characters) — consider adding detail")

    if item.scope == "global" and item.domain:
        warnings.append("Scope is 'global' but a domain is set — consider scope='domain'")

    return {"valid": valid, "warnings": warnings}


# ── Rule Application ──────────────────────────────────────────────────


def apply_rules_to_plan(
    plan_steps: list[dict[str, Any]],
    rules: list[MemoryItem],
) -> list[dict[str, Any]]:
    """Apply memory rules to a list of planned ActionStep dicts.

    For each step:
      1. Check all rules' avoid_actions — if a step's action_type
         appears in any rule's avoid list, the step is removed.
      2. Check all rules' preferred_actions — annotate steps with
         matching rule_applied metadata.

    Args:
        plan_steps: List of ActionStep dicts from the planner.
        rules:      List of relevant MemoryItems.

    Returns:
        Modified list of plan steps (some may be removed, others annotated).
    """
    if not rules:
        return plan_steps

    # Build a lookup of normalised avoid keywords
    avoid_lookup: list[tuple[str, str]] = []  # (normalised_keyword, memory_id)
    for rule in rules:
        for action in rule.avoid_actions:
            avoid_lookup.append((action.lower().strip(), rule.memory_id))

    # Build a lookup of normalised preferred keywords
    preferred_lookup: list[tuple[str, str]] = []
    for rule in rules:
        for action in rule.preferred_actions:
            preferred_lookup.append((action.lower().strip(), rule.memory_id))

    filtered_steps: list[dict[str, Any]] = []

    for step in plan_steps:
        action_type = str(step.get("action_type", "")).lower()
        reason = str(step.get("reason", "")).lower()
        target = str(step.get("target_text", "") or step.get("target_selector", "")).lower()
        step_text = f"{action_type} {reason} {target}"

        # Check avoid_actions
        removed = False
        for avoid_phrase, memory_id in avoid_lookup:
            if _phrase_matches(avoid_phrase, step_text):
                logger.info(
                    "Rule %s removed step [%s]: avoid_action '%s' matched",
                    memory_id, action_type, avoid_phrase,
                )
                removed = True
                break

        if removed:
            continue

        # Check preferred_actions — annotate matching steps
        for pref_phrase, memory_id in preferred_lookup:
            if _phrase_matches(pref_phrase, step_text):
                step["rule_applied"] = memory_id
                step["rule_note"] = f"Matches preferred_action: {pref_phrase}"
                break

        filtered_steps.append(step)

    if len(filtered_steps) < len(plan_steps):
        logger.info(
            "Rules removed %d of %d steps",
            len(plan_steps) - len(filtered_steps),
            len(plan_steps),
        )

    return filtered_steps


def _phrase_matches(phrase: str, text: str) -> bool:
    """Check if a phrase meaningfully matches text.

    Uses word overlap — at least 60% of the phrase's words must
    appear in the text for a match.
    """
    phrase_words = set(phrase.split())
    text_words = set(text.split())

    if not phrase_words:
        return False

    overlap = phrase_words & text_words
    overlap_ratio = len(overlap) / len(phrase_words)
    return overlap_ratio >= 0.6


# ── Context-Aware Retrieval ───────────────────────────────────────────


def get_relevant_rules_for_context(
    domain: str,
    goal: str,
    task_type: str,
    db_path: str,
) -> list[MemoryItem]:
    """Retrieve the most relevant rules for a given task context.

    Queries the semantic store for rules matching the domain or
    with global scope, filters by trigger_conditions keyword overlap,
    and returns the top 10 sorted by confidence descending.

    Args:
        domain:    Current page domain (e.g. "github.com").
        goal:      Natural-language task goal.
        task_type: Category of task ("mcq", "form", "navigation", "general").
        db_path:   Path to the SQLite database file.

    Returns:
        Up to 10 MemoryItem objects, most relevant first.
    """
    from memory.db import MemoryDB
    from memory.semantic_store import SemanticStore

    mdb = MemoryDB(db_path)
    mdb.init()
    store = SemanticStore(mdb)

    try:
        # Fetch domain-specific rules
        domain_rules = store.get_rules(domain=domain) if domain else []

        # Fetch global rules
        global_rules = store.get_rules(scope="global")

        # Combine and deduplicate by memory_id
        seen_ids: set[str] = set()
        all_rules: list[MemoryItem] = []

        for rule in domain_rules + global_rules:
            if rule.memory_id not in seen_ids:
                seen_ids.add(rule.memory_id)
                all_rules.append(rule)

        if not all_rules:
            return []

        # Score each rule by relevance
        goal_words = set(goal.lower().split())
        task_words = set(task_type.lower().split())
        query_words = goal_words | task_words

        scored: list[tuple[float, MemoryItem]] = []

        for rule in all_rules:
            score = rule.confidence * 0.3  # base: confidence

            # Trigger condition overlap
            for trigger in rule.trigger_conditions:
                trigger_words = set(trigger.lower().split())
                overlap = query_words & trigger_words
                if overlap:
                    score += 0.2 * (len(overlap) / max(len(trigger_words), 1))

            # Instruction keyword overlap
            inst_words = set(rule.instruction.lower().split())
            inst_overlap = query_words & inst_words
            score += 0.15 * (len(inst_overlap) / max(len(inst_words), 1))

            # Domain match boost
            if rule.domain and domain:
                if rule.domain.lower() == domain.lower():
                    score += 0.25
                elif domain.lower().endswith("." + rule.domain.lower()):
                    score += 0.1

            # Type boost (user_rule > site > semantic)
            mem_type = rule.type.value if isinstance(rule.type, MemoryType) else rule.type
            if mem_type == "user_rule":
                score += 0.15
            elif mem_type == "site":
                score += 0.1

            scored.append((score, rule))

        # Sort by score descending, take top 10
        scored.sort(key=lambda x: x[0], reverse=True)
        results = [rule for _score, rule in scored[:10] if _score > 0.01]

        logger.info(
            "get_relevant_rules_for_context(domain=%s, goal=%s, task=%s) → %d rules",
            domain, goal[:50], task_type, len(results),
        )

        return results

    finally:
        mdb.close()


# ── Legacy compat: parse_teaching (used by existing teach.py) ─────────


async def parse_teaching(
    prompt: TeachingPrompt,
    config: AgentConfig,
) -> MemoryItem:
    """Backward-compatible wrapper over parse_teaching_prompt().

    Called by the existing teach.py router.
    """
    return await parse_teaching_prompt(
        raw_text=prompt.raw_text,
        api_key=config.API_KEY,
        model=config.MODEL,
        domain=prompt.domain,
        scope=prompt.scope,
    )


# ── JSON parser ───────────────────────────────────────────────────────


def _parse_json(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from text, tolerating markdown fences."""
    text = text.strip()

    # Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown fences
    fenced = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    # Extract first { ... } block
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start: end + 1])
        except json.JSONDecodeError:
            pass

    return None
