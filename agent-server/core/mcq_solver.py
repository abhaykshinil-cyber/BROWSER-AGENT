"""
BrowserAgent — MCQ Solver (Phase 6)

AI-powered answering engine for multiple-choice questions detected
on web pages.  Calls Claude with the questions, user instructions,
topic context, and optional screenshot for visual layout understanding.

Handles:
  • Single-select (radio, dropdown, card)
  • Multi-select (checkbox — "choose all that apply")
  • Explicit user answers ("select A/B/C/D")
  • Context-informed answers (user provides topic / subject)
  • Memory-rule-driven answers (stored rules override AI)

Returns a list of Answer dicts ready to be applied by mcq-detector.js.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from config import AgentConfig
from schemas import MemoryItem

logger = logging.getLogger("browseragent.mcq_solver")

# ── Load system prompt ────────────────────────────────────────────────

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "mcq_solver.txt"

def _load_system_prompt() -> str:
    """Load the MCQ solver prompt from disk, with a built-in fallback."""
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("MCQ solver prompt not found at %s — using fallback", _PROMPT_PATH)
        return (
            "You are an expert MCQ answering agent. "
            "Answer every question in the provided JSON. "
            "Reply with ONLY a JSON object: {\"answers\": [{\"qIdx\": 0, \"selected\": [1], "
            "\"reasoning\": \"...\", \"confidence\": 0.9, \"source\": \"ai_knowledge\"}]}"
        )


SYSTEM_PROMPT = _load_system_prompt()


# ── Answer Type ───────────────────────────────────────────────────────

class Answer:
    """Result of solving a single question."""

    __slots__ = ("question_id", "selected_indices", "reasoning", "confidence", "source")

    def __init__(
        self,
        question_id: str | int,
        selected_indices: list[int],
        reasoning: str = "",
        confidence: float = 0.5,
        source: str = "ai_knowledge",
    ) -> None:
        self.question_id = question_id
        self.selected_indices = selected_indices
        self.reasoning = reasoning
        self.confidence = confidence
        self.source = source

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id":      self.question_id,
            "selected_indices": self.selected_indices,
            "reasoning":        self.reasoning,
            "confidence":       self.confidence,
            "source":           self.source,
        }


# ── User instruction parser ──────────────────────────────────────────

# "select A", "choose B", "answer C", "pick D", "option E"
_LETTER_PATTERN = re.compile(
    r"(?:select|choose|answer|pick|option)\s+([A-Ea-e])\b", re.I
)

# "select all A, B, D" or "select A and C"
_MULTI_LETTER_PATTERN = re.compile(
    r"(?:select|choose)\s+(?:all\s+)?([A-Ea-e](?:\s*(?:,|\band\b|&)\s*[A-Ea-e])+)", re.I
)

# "the answer is Paris" / "the correct answer is 42"
_ANSWER_TEXT_PATTERN = re.compile(
    r"(?:the\s+)?(?:correct\s+)?answer\s+(?:is|should\s+be)\s+[\"']?(.+?)[\"']?\s*$", re.I
)


def _letter_to_index(letter: str) -> int:
    """Convert A→0, B→1, C→2, D→3, E→4."""
    return ord(letter.upper()) - ord("A")


def parse_user_instruction(
    instruction: str,
    questions: list[dict[str, Any]],
) -> dict[int, Answer]:
    """Parse explicit user answers from the instruction text.

    Returns a dict of qIdx → Answer for any questions the user
    explicitly answered.  These take HIGHEST priority.
    """
    if not instruction:
        return {}

    overrides: dict[int, Answer] = {}
    instruction = instruction.strip()

    # ── Per-question "for question N, select X" ───────────────────
    per_q = re.findall(
        r"(?:for\s+)?(?:question|q)\s*#?\s*(\d+)\s*[,:]\s*(?:select|choose|answer|pick)\s+([A-Ea-e])",
        instruction, re.I
    )
    for q_num, letter in per_q:
        idx = int(q_num)
        # Questions may be 1-indexed in user speech
        if idx >= 1 and idx <= len(questions):
            overrides[idx - 1] = Answer(
                question_id=idx - 1,
                selected_indices=[_letter_to_index(letter)],
                reasoning=f"User explicitly selected option {letter.upper()}.",
                confidence=1.0,
                source="user_instruction",
            )

    if overrides:
        return overrides

    # ── Multi-letter ("select A, C, D" / "select A and C") ─────────
    mm = _MULTI_LETTER_PATTERN.search(instruction)
    if mm:
        letters = re.findall(r"[A-Ea-e]", mm.group(0))
        # Remove the verb itself if it starts with a letter (e.g. 'a' in 'and')
        # Only keep standalone single letters
        letters = re.findall(r"(?<![a-z])[A-Ea-e](?![a-z])", mm.group(0), re.I)
        if len(letters) >= 2:
            indices = [_letter_to_index(l) for l in letters]
            for q in questions:
                q_idx = q.get("qIdx", q.get("index", 0))
                if q.get("answered"):
                    continue
                overrides[q_idx] = Answer(
                    question_id=q_idx,
                    selected_indices=indices,
                    reasoning=f"User instructed: select options {', '.join(l.upper() for l in letters)}.",
                    confidence=1.0,
                    source="user_instruction",
                )
            return overrides

    # ── Global single letter ("select B") ─────────────────────────
    m = _LETTER_PATTERN.search(instruction)
    if m:
        idx = _letter_to_index(m.group(1))
        # Apply to ALL unanswered questions
        for q in questions:
            q_idx = q.get("qIdx", q.get("index", 0))
            if q.get("answered"):
                continue
            overrides[q_idx] = Answer(
                question_id=q_idx,
                selected_indices=[idx],
                reasoning=f"User instructed: select option {m.group(1).upper()} for all questions.",
                confidence=1.0,
                source="user_instruction",
            )
        return overrides

    # ── "The answer is <text>" ────────────────────────────────────
    mt = _ANSWER_TEXT_PATTERN.search(instruction)
    if mt:
        answer_text = mt.group(1).strip().lower()
        for q in questions:
            q_idx = q.get("qIdx", q.get("index", 0))
            if q.get("answered"):
                continue
            for opt in q.get("options", []):
                if opt.get("text", "").strip().lower() == answer_text:
                    overrides[q_idx] = Answer(
                        question_id=q_idx,
                        selected_indices=[opt.get("idx", 0)],
                        reasoning=f"User specified answer text: '{mt.group(1).strip()}'.",
                        confidence=1.0,
                        source="user_instruction",
                    )
                    break

    return overrides


# ── Memory rule matcher ──────────────────────────────────────────────

def match_memory_rules(
    questions: list[dict[str, Any]],
    memories: list[dict[str, Any]],
) -> dict[int, Answer]:
    """Check if any stored memory rules directly answer a question.

    A rule matches if its trigger_conditions text overlaps significantly
    with the question text.
    """
    if not memories:
        return {}

    overrides: dict[int, Answer] = {}

    for mem in memories:
        instruction = mem.get("instruction", "")
        triggers = mem.get("trigger_conditions", [])
        preferred = mem.get("preferred_actions", [])

        if not instruction and not preferred:
            continue

        for q in questions:
            q_idx = q.get("qIdx", q.get("index", 0))
            if q.get("answered"):
                continue

            q_text = q.get("text", "").lower()

            # Check if any trigger matches the question text
            matched = False
            for trigger in triggers:
                if isinstance(trigger, str) and trigger.lower() in q_text:
                    matched = True
                    break

            if not matched:
                # Check instruction text overlap
                inst_words = set(instruction.lower().split())
                q_words = set(q_text.split())
                overlap = inst_words & q_words
                if len(overlap) < 3:
                    continue

            # Try to extract an answer from preferred_actions
            for action in preferred:
                action_str = str(action).lower()
                for opt in q.get("options", []):
                    if opt.get("text", "").strip().lower() in action_str:
                        overrides[q_idx] = Answer(
                            question_id=q_idx,
                            selected_indices=[opt.get("idx", 0)],
                            reasoning=f"Memory rule: {instruction[:100]}",
                            confidence=0.9,
                            source="user_rule",
                        )
                        break
                if q_idx in overrides:
                    break

    return overrides


# ── Claude API call ──────────────────────────────────────────────────

def _build_user_message(
    questions: list[dict[str, Any]],
    user_instruction: str,
    context: str,
    memories: list[dict[str, Any]],
    page_url: str = "",
    page_title: str = "",
) -> str:
    """Build the user-message JSON envelope for the Claude call."""
    # Simplify questions for the prompt (strip selectors, keep text)
    simplified_qs = []
    for q in questions:
        if q.get("answered"):
            continue
        sq = {
            "qIdx": q.get("qIdx", q.get("index", 0)),
            "text": q.get("text", ""),
            "type": q.get("type", "radio"),
            "options": [
                {"idx": opt.get("idx", i), "text": opt.get("text", "")}
                for i, opt in enumerate(q.get("options", []))
            ],
            "answered": False,
        }
        simplified_qs.append(sq)

    payload = {
        "questions": simplified_qs,
        "user_instruction": user_instruction or "",
        "context": context or "",
        "page_url": page_url,
        "page_title": page_title,
        "relevant_memories": [
            {"instruction": m.get("instruction", ""), "type": m.get("type", "")}
            for m in (memories or [])[:5]
        ],
    }

    return json.dumps(payload, ensure_ascii=False)


def _build_gemini_content(
    user_text: str,
    screenshot_b64: Optional[str] = None,
) -> Any:
    """Build the user content for the Gemini API call.

    Returns a plain string for text-only requests, or a list of parts
    for multimodal requests (when a screenshot is provided).

    Gemini inline-image format:
        [{"inline_data": {"mime_type": "image/png", "data": <base64-str>}}, <text>]
    """
    if not screenshot_b64:
        return user_text

    return [
        {"inline_data": {"mime_type": "image/png", "data": screenshot_b64}},
        user_text,
    ]


def _parse_response(raw: str) -> list[dict[str, Any]]:
    """Extract the answers array from Claude's response.

    Tries multiple strategies to handle partial or markdown-wrapped JSON.
    """
    text = raw.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    # Strategy 1: direct parse
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "answers" in data:
            return data["answers"]
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    # Strategy 2: find the first { ... } block containing "answers"
    brace_start = text.find("{")
    if brace_start >= 0:
        depth = 0
        for i in range(brace_start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[brace_start:i + 1]
                    try:
                        data = json.loads(candidate)
                        if isinstance(data, dict) and "answers" in data:
                            return data["answers"]
                    except json.JSONDecodeError:
                        pass
                    break

    # Strategy 3: find [ ... ] array of answers
    bracket_start = text.find("[")
    if bracket_start >= 0:
        depth = 0
        for i in range(bracket_start, len(text)):
            if text[i] == "[":
                depth += 1
            elif text[i] == "]":
                depth -= 1
                if depth == 0:
                    candidate = text[bracket_start:i + 1]
                    try:
                        data = json.loads(candidate)
                        if isinstance(data, list):
                            return data
                    except json.JSONDecodeError:
                        pass
                    break

    logger.error("Failed to parse MCQ solver response: %s", text[:500])
    return []


# ── Main solver ──────────────────────────────────────────────────────

async def solve_questions(
    questions: list[dict[str, Any]],
    user_instruction: str = "",
    context: str = "",
    memories: list[dict[str, Any]] | None = None,
    screenshot_b64: str | None = None,
    page_url: str = "",
    page_title: str = "",
    config: AgentConfig | None = None,
) -> list[Answer]:
    """Solve a batch of MCQ questions using Claude.

    Priority chain:
      1. User explicit instruction (letter or text match) → highest
      2. Memory rules matching question text
      3. Claude AI with context, memories, and optional screenshot

    Args:
        questions:        List of question dicts from mcq-detector.js
        user_instruction: Free-text instruction from the user
        context:          Topic / subject context for the quiz
        memories:         Relevant stored memories
        screenshot_b64:   Optional base64 PNG of the page
        page_url:         URL of the quiz page
        page_title:       Title of the quiz page
        config:           Server configuration

    Returns:
        List of Answer objects ready for application.
    """
    if config is None:
        config = AgentConfig()

    memories = memories or []

    # Filter to unanswered questions only
    unanswered = [q for q in questions if not q.get("answered", False)]
    if not unanswered:
        logger.info("No unanswered questions to solve")
        return []

    logger.info(
        "Solving %d questions (instruction=%s, context=%s)",
        len(unanswered),
        user_instruction[:60] if user_instruction else "none",
        context[:60] if context else "none",
    )

    # ── Phase 1: Parse user overrides ─────────────────────────────
    user_overrides = parse_user_instruction(user_instruction, unanswered)
    logger.info("User overrides: %d questions", len(user_overrides))

    # ── Phase 2: Match memory rules ───────────────────────────────
    memory_overrides = match_memory_rules(unanswered, memories)
    logger.info("Memory rule matches: %d questions", len(memory_overrides))

    # ── Phase 3: Identify questions still needing AI ──────────────
    ai_needed = []
    for q in unanswered:
        q_idx = q.get("qIdx", q.get("index", 0))
        if q_idx not in user_overrides and q_idx not in memory_overrides:
            ai_needed.append(q)

    # ── Phase 4: Call Claude for remaining questions ──────────────
    ai_answers: dict[int, Answer] = {}

    if ai_needed:
        if not config.API_KEY:
            logger.warning("No API key — returning low-confidence guesses")
            for q in ai_needed:
                q_idx = q.get("qIdx", q.get("index", 0))
                # Default: select first option
                ai_answers[q_idx] = Answer(
                    question_id=q_idx,
                    selected_indices=[0],
                    reasoning="No API key available — defaulting to first option.",
                    confidence=0.1,
                    source="ai_knowledge",
                )
        else:
            try:
                from core.llm import call_gemini_async, GEMINI_MAIN

                user_msg = _build_user_message(
                    ai_needed, user_instruction, context, memories,
                    page_url, page_title,
                )
                gemini_content = _build_gemini_content(user_msg, screenshot_b64)

                raw_text = await call_gemini_async(
                    api_key=config.API_KEY,
                    model_name=config.MODEL or GEMINI_MAIN,
                    system_prompt=SYSTEM_PROMPT,
                    user_content=gemini_content,
                    max_tokens=config.MAX_TOKENS,
                )

                logger.debug("Gemini MCQ response: %s", raw_text[:500])

                parsed = _parse_response(raw_text)

                for ans_dict in parsed:
                    q_idx = ans_dict.get("qIdx", -1)
                    selected = ans_dict.get("selected", [])
                    reasoning = ans_dict.get("reasoning", "")
                    confidence = ans_dict.get("confidence", 0.5)
                    source = ans_dict.get("source", "ai_knowledge")

                    if q_idx < 0:
                        continue

                    # Validate selected indices against options
                    q_match = None
                    for q in ai_needed:
                        if q.get("qIdx", q.get("index", 0)) == q_idx:
                            q_match = q
                            break

                    if q_match:
                        num_opts = len(q_match.get("options", []))
                        selected = [i for i in selected if 0 <= i < num_opts]
                        if not selected:
                            selected = [0]  # Fallback to first option

                    ai_answers[q_idx] = Answer(
                        question_id=q_idx,
                        selected_indices=selected,
                        reasoning=reasoning,
                        confidence=confidence,
                        source=source,
                    )

            except Exception as e:
                logger.error("Claude MCQ call failed: %s", e)
                # Fallback: select first option for all remaining
                for q in ai_needed:
                    q_idx = q.get("qIdx", q.get("index", 0))
                    ai_answers[q_idx] = Answer(
                        question_id=q_idx,
                        selected_indices=[0],
                        reasoning=f"AI call failed ({str(e)[:80]}) — defaulting to first option.",
                        confidence=0.1,
                        source="ai_knowledge",
                    )

    # ── Phase 5: Merge all answers (priority: user > memory > AI) ─
    final: list[Answer] = []

    for q in unanswered:
        q_idx = q.get("qIdx", q.get("index", 0))

        if q_idx in user_overrides:
            final.append(user_overrides[q_idx])
        elif q_idx in memory_overrides:
            final.append(memory_overrides[q_idx])
        elif q_idx in ai_answers:
            final.append(ai_answers[q_idx])
        else:
            # Should not happen, but safety fallback
            final.append(Answer(
                question_id=q_idx,
                selected_indices=[0],
                reasoning="No answer source available — selecting first option.",
                confidence=0.05,
                source="ai_knowledge",
            ))

    logger.info(
        "Solved %d questions: %d user, %d memory, %d AI",
        len(final),
        len(user_overrides),
        len(memory_overrides),
        len(ai_answers),
    )

    return final


# ── Convenience: sync wrapper ─────────────────────────────────────────

def solve_questions_sync(
    questions: list[dict[str, Any]],
    user_instruction: str = "",
    context: str = "",
    memories: list[dict[str, Any]] | None = None,
    screenshot_b64: str | None = None,
    page_url: str = "",
    page_title: str = "",
    config: AgentConfig | None = None,
) -> list[dict[str, Any]]:
    """Synchronous wrapper that returns plain dicts.

    Useful for testing or non-async callers.
    """
    import asyncio

    answers = asyncio.run(solve_questions(
        questions=questions,
        user_instruction=user_instruction,
        context=context,
        memories=memories,
        screenshot_b64=screenshot_b64,
        page_url=page_url,
        page_title=page_title,
        config=config,
    ))

    return [a.to_dict() for a in answers]
