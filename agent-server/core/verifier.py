"""
BrowserAgent — Verifier (Gemini LLM)

Lightweight verification module that checks whether an executed plan
achieved the user's goal.  Uses gemini-2.0-flash-lite for speed,
falling back to the configured model.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from core.llm import call_gemini, GEMINI_FAST, GEMINI_MAIN

from config import AgentConfig
from schemas import (
    ActionResult,
    AgentTask,
    PageContext,
    VerifyResponse,
)

logger = logging.getLogger("browseragent.verifier")

# ── Preferred fast model for verification ─────────────────────────────

_FAST_MODEL = GEMINI_FAST   # gemini-2.0-flash-lite

# ── System Prompt ─────────────────────────────────────────────────────

VERIFIER_SYSTEM = """\
You are BrowserAgent Verifier — a concise evaluator that checks whether
a browser automation task was completed successfully.

You receive:
1. The original goal.
2. The steps that were executed and their results.
3. The current page state after execution.
4. An optional expected outcome description.

RESPONSE FORMAT (strict JSON — no markdown, no extra text):
{
  "success": true,
  "confidence": 0.92,
  "summary": "The form was submitted and the confirmation page appeared.",
  "issues": [],
  "retry_suggestion": null
}

RULES:
- success: true only if the goal appears fully achieved.
- confidence: 0.0–1.0.
- summary: 1-2 sentence human-readable summary.
- issues: list of strings describing any problems detected.
- retry_suggestion: a short instruction for retrying if the task failed, or null.
- Return ONLY the JSON object.
"""


async def verify(
    task: AgentTask,
    steps_executed: list[ActionResult],
    page_context: PageContext,
    expected_outcome: Optional[str],
    config: AgentConfig,
) -> VerifyResponse:
    """Ask Claude whether the executed steps achieved the task goal.

    Tries the fast model first; if unavailable, falls back to the
    configured model.
    """
    if not config.API_KEY:
        return VerifyResponse(
            success=False,
            confidence=0.0,
            summary="No API key configured — cannot verify.",
            issues=["BROWSERAGENT_API_KEY not set."],
        )

    user_content = _build_verify_prompt(task, steps_executed, page_context, expected_outcome)

    # Try fast model first, fall back to configured model
    for model in (_FAST_MODEL, config.MODEL or GEMINI_MAIN):
        try:
            raw_text = call_gemini(
                api_key=config.API_KEY,
                model_name=model,
                system_prompt=VERIFIER_SYSTEM,
                user_content=user_content,
                max_tokens=1024,
            )
            break
        except Exception as exc:
            logger.warning("Gemini model %s failed: %s — trying fallback", model, exc)
            raw_text = None
    else:
        return VerifyResponse(
            success=False,
            confidence=0.0,
            summary="Verification failed: all Gemini models unavailable.",
            issues=["All Gemini models failed."],
        )

    logger.debug("Verifier raw response:\n%s", (raw_text or "")[:1000])

    parsed = _parse_json(raw_text)
    if parsed is None:
        return VerifyResponse(
            success=False,
            confidence=0.0,
            summary=f"Could not parse verifier response. Raw: {raw_text[:300]}",
            issues=["Malformed verifier JSON."],
        )

    return VerifyResponse(
        success=bool(parsed.get("success", False)),
        confidence=max(0.0, min(1.0, float(parsed.get("confidence", 0.5)))),
        summary=parsed.get("summary", ""),
        issues=parsed.get("issues", []),
        suggested_retry_plan=None,
    )


# ── Prompt builder ────────────────────────────────────────────────────


def _build_verify_prompt(
    task: AgentTask,
    steps: list[ActionResult],
    page_context: PageContext,
    expected_outcome: Optional[str],
) -> str:
    """Build the user-turn content for the verification request."""
    sections: list[str] = []

    sections.append(f"## ORIGINAL GOAL\n{task.goal}")
    if expected_outcome:
        sections.append(f"## EXPECTED OUTCOME\n{expected_outcome}")

    # Steps summary
    step_lines: list[str] = []
    for i, s in enumerate(steps, 1):
        status = "✓" if s.success else "✗"
        err = f" — Error: {s.error}" if s.error else ""
        step_lines.append(f"{i}. [{status}] {s.action_taken}{err}")
    sections.append("## EXECUTED STEPS\n" + "\n".join(step_lines))

    # Current page state
    sections.append(
        f"## CURRENT PAGE STATE\n"
        f"URL: {page_context.url}\n"
        f"Title: {page_context.title}\n"
        f"Body text (trimmed): {page_context.body_text[:3000]}"
    )

    return "\n\n".join(sections)


# ── JSON parser ───────────────────────────────────────────────────────


def _parse_json(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from text, tolerating markdown fences."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    return None
