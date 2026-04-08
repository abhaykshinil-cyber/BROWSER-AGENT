"""
BrowserAgent — MCQ Solver API Router (Phase 6)

POST /mcq/solve   — Solve MCQ questions detected on a page.
POST /mcq/detect   — (proxy) Trigger detection via content script relay.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config import AgentConfig
from core.mcq_solver import solve_questions, Answer

logger = logging.getLogger("browseragent.api.mcq")

router = APIRouter(prefix="/mcq", tags=["mcq"])


# ── Request / Response Models ─────────────────────────────────────────

class MCQOption(BaseModel):
    idx: int = 0
    text: str = ""

class MCQQuestion(BaseModel):
    qIdx: int = 0
    text: str = ""
    type: str = "radio"
    options: list[MCQOption] = Field(default_factory=list)
    answered: bool = False

class MCQSolveRequest(BaseModel):
    questions: list[MCQQuestion]
    user_instruction: str = ""
    context: str = ""
    memories: list[dict[str, Any]] = Field(default_factory=list)
    screenshot_b64: Optional[str] = None
    page_url: str = ""
    page_title: str = ""

class MCQAnswerItem(BaseModel):
    question_id: int | str
    selected_indices: list[int]
    reasoning: str = ""
    confidence: float = 0.5
    source: str = "ai_knowledge"

class MCQSolveResponse(BaseModel):
    answers: list[MCQAnswerItem]
    total_questions: int
    answered_count: int
    skipped_count: int


# ── Routes ────────────────────────────────────────────────────────────

@router.post("/solve", response_model=MCQSolveResponse)
async def solve_mcq(request: MCQSolveRequest):
    """Solve a batch of MCQ questions.

    The frontend sends detected questions from mcq-detector.js.
    This endpoint calls the MCQ solver (which uses Claude) and
    returns selected option indices for each question.
    """
    if not request.questions:
        raise HTTPException(status_code=400, detail="No questions provided")

    # Convert Pydantic models to plain dicts for the solver
    questions_dicts = []
    for q in request.questions:
        questions_dicts.append({
            "qIdx": q.qIdx,
            "text": q.text,
            "type": q.type,
            "options": [{"idx": o.idx, "text": o.text} for o in q.options],
            "answered": q.answered,
        })

    unanswered_count = sum(1 for q in request.questions if not q.answered)
    skipped_count = sum(1 for q in request.questions if q.answered)

    try:
        config = AgentConfig()
        answers: list[Answer] = await solve_questions(
            questions=questions_dicts,
            user_instruction=request.user_instruction,
            context=request.context,
            memories=request.memories,
            screenshot_b64=request.screenshot_b64,
            page_url=request.page_url,
            page_title=request.page_title,
            config=config,
        )

        answer_items = [
            MCQAnswerItem(
                question_id=a.question_id,
                selected_indices=a.selected_indices,
                reasoning=a.reasoning,
                confidence=a.confidence,
                source=a.source,
            )
            for a in answers
        ]

        logger.info(
            "Solved %d/%d questions (skipped %d already answered)",
            len(answer_items), len(request.questions), skipped_count,
        )

        return MCQSolveResponse(
            answers=answer_items,
            total_questions=len(request.questions),
            answered_count=len(answer_items),
            skipped_count=skipped_count,
        )

    except Exception as e:
        logger.error("MCQ solve failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"MCQ solver error: {str(e)}")
