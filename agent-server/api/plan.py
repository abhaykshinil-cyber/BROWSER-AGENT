"""
BrowserAgent — Plan API Router

POST /plan  →  generate an action plan via the LLM planner.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from schemas import PlanRequest, PlanResponse
from core.planner import plan as run_planner

logger = logging.getLogger("browseragent.api.plan")
router = APIRouter()


@router.post("/plan", response_model=PlanResponse)
async def create_plan(body: PlanRequest, request: Request):
    """Generate an ordered action plan for the given task and page context.

    The planner calls Claude with the task goal, visible DOM elements,
    page text, and any relevant long-term memories.  It returns an
    ordered list of ``ActionStep`` objects the extension should execute.

    The ``requires_confirmation`` flag is set to ``True`` when the plan
    contains potentially destructive steps (submit, login, purchase,
    delete, etc.).
    """
    config = request.app.state.config
    logger.info("Plan requested — goal: %s", body.task.goal)

    response = await run_planner(
        task=body.task,
        page_context=body.page_context,
        memories=body.relevant_memories,
        config=config,
    )

    logger.info(
        "Plan generated — %d steps, confidence=%.2f, confirm=%s",
        len(response.plan),
        response.confidence,
        response.requires_confirmation,
    )
    return response
