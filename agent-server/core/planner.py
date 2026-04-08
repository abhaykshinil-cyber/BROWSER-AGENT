"""
BrowserAgent — Planner (Gemini LLM)

Builds a system prompt from the task, page context, and relevant memories,
calls Gemini, and parses the response into ActionStep objects.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from core.llm import call_gemini, GEMINI_MAIN

from config import AgentConfig
from schemas import (
    ActionStep,
    ActionType,
    AgentTask,
    MemoryItem,
    PageContext,
    PlanResponse,
)

logger = logging.getLogger("browseragent.planner")

# ── Destructive-action keywords that trigger confirmation ─────────────

_DESTRUCTIVE_KEYWORDS = {
    "submit", "login", "sign in", "sign-in", "purchase", "buy", "pay",
    "delete", "remove", "unsubscribe", "cancel subscription", "checkout",
    "confirm order", "place order", "send payment", "transfer",
}

# ── System Prompt ─────────────────────────────────────────────────────

PLANNER_SYSTEM = """\
You are BrowserAgent Planner — an expert browser automation AI.

ROLE: Given a user's goal, the current page context, and any stored memories,
produce an ordered JSON list of atomic browser actions that accomplish the goal.

AVAILABLE ACTIONS (use the exact string as action_type):
  SCAN        — Re-scan the page for elements.
  SELECT      — Select a radio, checkbox, or dropdown option.
  CLICK       — Click an element by CSS selector or visible text.
  TYPE        — Type text into an input, textarea, or contenteditable.
  SCROLL      — Scroll the viewport (direction: up/down/left/right, amount in px).
  NAVIGATE    — Navigate to a URL.
  EXTRACT     — Extract text from a selector or the full page.
  SCREENSHOT  — Capture a viewport screenshot.
  SUBMIT      — Click the best submit/finish/done button on the page.
  WAIT        — Wait a specified number of milliseconds.
  SWITCH_TAB  — Switch to another browser tab.

RESPONSE FORMAT (strict JSON — no markdown fences, no comments):
{
  "plan": [
    {
      "action_type": "CLICK",
      "target_selector": "#some-button",
      "target_text": "Submit",
      "input_value": null,
      "reason": "Click the submit button to send the form"
    }
  ],
  "reasoning": "Step-by-step explanation of the plan",
  "confidence": 0.85
}

RULES:
1. Each step MUST have action_type.  Include target_selector when possible.
2. If target_selector might be fragile, also include target_text as fallback.
3. For TYPE actions, put the text in input_value.
4. For SCROLL, put direction and amount in input_value like "down 500".
5. For NAVIGATE, put the full URL in input_value.
6. For WAIT, put milliseconds in input_value.
7. Keep plans short — prefer fewer precise steps over many uncertain ones.
8. confidence is 0.0–1.0 indicating your certainty the plan will work.
9. Return ONLY the JSON object.  No explanatory text outside the JSON.
"""


def build_prompt(
    task: AgentTask,
    page_context: PageContext,
    memories: list[MemoryItem],
) -> str:
    """Assemble the user-turn content for Claude.

    Combines the goal, page snapshot (URL, title, truncated body text,
    interactive elements), and any relevant memories into a single string.
    """
    sections: list[str] = []

    # -- Goal
    sections.append(f"## USER GOAL\n{task.goal}")
    if task.context:
        sections.append(f"Additional context: {task.context}")

    # -- Page context
    sections.append(f"\n## CURRENT PAGE\nURL: {page_context.url}\nTitle: {page_context.title}")

    # Visible elements (compact table)
    if page_context.visible_elements:
        rows: list[str] = []
        for el in page_context.visible_elements[:60]:
            attrs = el.attributes
            row_parts = [
                el.tag,
                attrs.get("type", ""),
                el.selector,
                el.text[:80] if el.text else "",
            ]
            rows.append(" | ".join(row_parts))
        sections.append("## INTERACTIVE ELEMENTS\ntag | type | selector | text\n" + "\n".join(rows))

    # Body text (trimmed)
    body = page_context.body_text[:6000] if page_context.body_text else ""
    if body:
        sections.append(f"## PAGE TEXT (trimmed)\n{body}")

    # -- Memories
    if memories:
        mem_lines: list[str] = []
        for m in memories:
            mem_lines.append(
                f"- [{m.type.value}] {m.instruction}"
                + (f" (domain: {m.domain})" if m.domain else "")
            )
        sections.append("## RELEVANT MEMORIES\n" + "\n".join(mem_lines))

    # -- Task settings
    if task.settings.allowed_domains:
        sections.append(f"DOMAIN RESTRICTION: Only navigate to {task.settings.allowed_domains}")

    return "\n\n".join(sections)


async def plan(
    task: AgentTask,
    page_context: PageContext,
    memories: list[MemoryItem],
    config: AgentConfig,
) -> PlanResponse:
    """Call Claude to generate an action plan and return a PlanResponse.

    Handles malformed JSON gracefully by falling back to an empty plan
    with an error in the reasoning field.
    """
    if not config.API_KEY:
        logger.warning("No API key configured — returning empty plan")
        return PlanResponse(
            plan=[],
            reasoning="No API key set.  Configure BROWSERAGENT_API_KEY.",
            confidence=0.0,
            requires_confirmation=False,
        )

    user_content = build_prompt(task, page_context, memories)

    try:
        raw_text = call_gemini(
            api_key=config.API_KEY,
            model_name=config.MODEL or GEMINI_MAIN,
            system_prompt=PLANNER_SYSTEM,
            user_content=user_content,
            max_tokens=config.MAX_TOKENS,
        )
    except Exception as exc:
        logger.error("Gemini API error: %s", exc)
        return PlanResponse(
            plan=[],
            reasoning=f"Gemini API error: {exc}",
            confidence=0.0,
            requires_confirmation=False,
        )

    logger.debug("Gemini raw response:\n%s", raw_text[:2000])

    parsed = _parse_plan_json(raw_text)
    if parsed is None:
        return PlanResponse(
            plan=[],
            reasoning=f"Failed to parse Claude response as JSON. Raw: {raw_text[:500]}",
            confidence=0.0,
            requires_confirmation=True,
        )

    # Build ActionStep list
    steps = _build_steps(parsed.get("plan", []))
    reasoning = parsed.get("reasoning", "")
    confidence = float(parsed.get("confidence", 0.8))
    confidence = max(0.0, min(1.0, confidence))

    # Determine if confirmation is required
    needs_confirm = _needs_confirmation(steps, task)

    return PlanResponse(
        plan=steps,
        reasoning=reasoning,
        confidence=confidence,
        requires_confirmation=needs_confirm,
    )


# ── Parsing helpers ───────────────────────────────────────────────────


def _parse_plan_json(text: str) -> dict[str, Any] | None:
    """Best-effort JSON extraction from Claude's response.

    Tries direct parse first, then looks for a JSON object inside
    markdown fences or between { and }.
    """
    text = text.strip()

    # 1. Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Strip markdown fences
    fenced = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    # 3. Find outermost { … }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    logger.warning("Could not extract JSON from planner response")
    return None


def _build_steps(raw_steps: list[dict]) -> list[ActionStep]:
    """Convert raw dicts from Claude into validated ActionStep objects."""
    steps: list[ActionStep] = []
    for raw in raw_steps:
        try:
            action_type_str = raw.get("action_type", "").upper()
            # Validate against enum
            try:
                action_type = ActionType(action_type_str)
            except ValueError:
                logger.warning("Unknown action_type '%s', skipping step", action_type_str)
                continue

            step = ActionStep(
                step_id=raw.get("step_id", uuid.uuid4().hex),
                action_type=action_type,
                target_selector=raw.get("target_selector"),
                target_text=raw.get("target_text"),
                input_value=raw.get("input_value"),
                reason=raw.get("reason", ""),
                confidence=max(0.0, min(1.0, float(raw.get("confidence", 0.9)))),
            )
            steps.append(step)
        except Exception as exc:
            logger.warning("Skipping malformed step %s: %s", raw, exc)

    return steps


def _needs_confirmation(steps: list[ActionStep], task: AgentTask) -> bool:
    """Determine whether a plan requires user confirmation before execution."""
    # User explicitly requested confirmation
    if task.settings.require_confirmation:
        return True

    # Check for destructive action types
    for step in steps:
        if step.action_type == ActionType.SUBMIT:
            return True
        if step.action_type == ActionType.NAVIGATE:
            return True

        # Check reason & target_text for destructive keywords
        text_to_check = " ".join(
            filter(None, [step.reason, step.target_text, step.input_value])
        ).lower()
        for keyword in _DESTRUCTIVE_KEYWORDS:
            if keyword in text_to_check:
                return True

    return False
