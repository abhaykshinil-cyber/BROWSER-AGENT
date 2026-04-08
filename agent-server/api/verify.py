"""
BrowserAgent — Verify API Router

POST /verify  →  check whether an executed plan achieved the goal.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from schemas import (
    ActionResult,
    AgentTask,
    PageContext,
    VerifyResponse,
)
from core.verifier import verify as run_verifier
from core.learning_engine import adjust_memory_confidence, learn_from_run

logger = logging.getLogger("browseragent.api.verify")
router = APIRouter()


class VerifyRequestBody(BaseModel):
    """Request body for the /verify endpoint."""

    task: AgentTask = Field(..., description="The original task.")
    steps_executed: list[ActionResult] = Field(
        ..., description="Results of every step that was executed."
    )
    page_context: PageContext = Field(
        ..., description="Current page state after execution."
    )
    expected_outcome: Optional[str] = Field(
        default=None,
        description="Natural-language description of the desired end state.",
    )


class VerifyResponseBody(BaseModel):
    """Response body for the /verify endpoint."""

    verified: bool = Field(..., description="Whether the goal was achieved.")
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description="Confidence in the verification result.",
    )
    reason: str = Field(default="", description="Explanation of the result.")
    retry_suggestion: Optional[str] = Field(
        default=None,
        description="Suggestion for retrying the task if it failed.",
    )


@router.post("/verify", response_model=VerifyResponseBody)
async def verify_plan(body: VerifyRequestBody, request: Request):
    """Verify whether an executed plan achieved the user's goal.

    Calls Claude (Haiku for speed, Opus as fallback) with the original
    goal, executed step results, and current page state.  Returns a
    compact verdict with confidence and retry suggestion.
    """
    config = request.app.state.config
    logger.info("Verify requested — task: %s", body.task.task_id)

    result: VerifyResponse = await run_verifier(
        task=body.task,
        steps_executed=body.steps_executed,
        page_context=body.page_context,
        expected_outcome=body.expected_outcome,
        config=config,
    )

    logger.info(
        "Verification complete — success=%s, confidence=%.2f",
        result.success,
        result.confidence,
    )

    # ── Post-verification learning hooks (fire-and-forget) ─────────────
    steps_as_dicts  = [s.model_dump() for s in body.steps_executed]
    memories_used   = body.task.memory_rules  # IDs of rules applied this run

    # 1. Adjust confidence of every memory rule used in this run
    if memories_used:
        try:
            adjust_memory_confidence(
                steps=steps_as_dicts,
                results=steps_as_dicts,
                memories_used=memories_used,
                db_path=config.DB_PATH,
            )
            logger.info("adjust_memory_confidence — updated %d rule(s)", len(memories_used))
        except Exception as exc:
            logger.warning("adjust_memory_confidence failed: %s", exc)

    # 2. Run full post-run learning in the background (generates new memory rules)
    domain = urlparse(body.page_context.url).netloc if body.page_context.url else ""

    async def _run_learning() -> None:
        try:
            new_ids = await asyncio.to_thread(
                learn_from_run,
                run_id=body.task.task_id,
                goal=body.task.goal,
                domain=domain,
                steps=steps_as_dicts,
                results=steps_as_dicts,
                db_path=config.DB_PATH,
                api_key=config.API_KEY,
                model=config.MODEL,
            )
            logger.info("learn_from_run — stored %d new rule(s)", len(new_ids))
        except Exception as exc:
            logger.warning("learn_from_run failed: %s", exc)

    asyncio.create_task(_run_learning())
    # ───────────────────────────────────────────────────────────────────

    return VerifyResponseBody(
        verified=result.success,
        confidence=result.confidence,
        reason=result.summary,
        retry_suggestion=result.issues[0] if result.issues else None,
    )
