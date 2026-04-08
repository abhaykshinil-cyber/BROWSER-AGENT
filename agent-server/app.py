"""
BrowserAgent — FastAPI Application (Phase 3)

Central application factory that:
  • Boots the FastAPI app with CORS for chrome-extension origins
  • Initialises the SQLite database on startup
  • Registers all API routers: /plan, /verify, /teach, /memory
  • Exposes /health, /run, /stop control endpoints
  • Gracefully shuts down on exit

Run with:
    cd agent-server
    uvicorn app:app --port 8765 --reload
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from config import AgentConfig, load_config
from db import Database

# ── Routers ───────────────────────────────────────────────────────────
from api.plan import router as plan_router
from api.verify import router as verify_router
from api.teach import router as teach_router
from api.memory import router as memory_router
from api.mcq import router as mcq_router

# ── Logging ───────────────────────────────────────────────────────────

logger = logging.getLogger("browseragent")

# ── Runtime State ─────────────────────────────────────────────────────


class RunState:
    """Mutable state for the currently executing task run."""

    def __init__(self) -> None:
        self.running: bool = False
        self.task_id: Optional[str] = None
        self.goal: Optional[str] = None
        self.steps_completed: int = 0
        self.started_at: Optional[str] = None
        self._cancel_event: asyncio.Event = asyncio.Event()

    def start(self, task_id: str, goal: str) -> None:
        self.running = True
        self.task_id = task_id
        self.goal = goal
        self.steps_completed = 0
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._cancel_event.clear()

    def stop(self) -> None:
        self.running = False
        self._cancel_event.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def to_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "task_id": self.task_id,
            "goal": self.goal,
            "steps_completed": self.steps_completed,
            "started_at": self.started_at,
        }


# ── Lifespan ──────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle for the application."""
    # ── Startup ───────────────────────────────────────────────────
    config = load_config()
    app.state.config = config
    app.state.run_state = RunState()

    # Configure logging
    log_level = logging.DEBUG if config.DEBUG else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        stream=sys.stdout,
    )

    logger.info("BrowserAgent server starting on port %d", config.SERVER_PORT)
    logger.info("Model:     %s", config.MODEL)
    logger.info("Database:  %s", config.DB_PATH)
    logger.info("Debug:     %s", config.DEBUG)

    if not config.API_KEY:
        logger.warning(
            "GEMINI_API_KEY is not set — LLM features will return stubs. "
            "Set the key in your .env file: GEMINI_API_KEY=AIza..."
        )

    # Initialise database
    db = Database(config.DB_PATH)
    await db.connect()
    app.state.db = db

    logger.info("BrowserAgent server ready ✓")

    yield

    # ── Shutdown ──────────────────────────────────────────────────
    logger.info("BrowserAgent server shutting down…")
    app.state.run_state.stop()
    await db.close()
    logger.info("Shutdown complete")


# ── App Factory ───────────────────────────────────────────────────────

app = FastAPI(
    title="BrowserAgent Server",
    version="0.3.0",
    description=(
        "AI browser agent backend — planning, verification, teaching, "
        "memory management, and MCQ solving.  Phase 6."
    ),
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────
# Allow:
#   • Any chrome-extension origin (chrome-extension://*)
#   • localhost for development
#   • Wildcard as a catch-all for early-stage development

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(chrome-extension://.*|http://localhost(:\d+)?|http://127\.0\.0\.1(:\d+)?)$",
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# ── Register Routers ─────────────────────────────────────────────────

app.include_router(plan_router,   tags=["Planning"])
app.include_router(verify_router, tags=["Verification"])
app.include_router(teach_router,  tags=["Teaching"])
app.include_router(memory_router, tags=["Memory"])
app.include_router(mcq_router,    tags=["MCQ Solver"])

# ── Health ────────────────────────────────────────────────────────────


@app.get("/health", tags=["System"])
async def health_check(request: Request):
    """Lightweight readiness probe.

    Returns server status, active model, database path, and whether
    a task is currently running.
    """
    config: AgentConfig = request.app.state.config
    run_state: RunState = request.app.state.run_state
    return {
        "status": "ok",
        "version": "0.3.0",
        "model": config.MODEL,
        "db_path": config.DB_PATH,
        "api_key_set": bool(config.API_KEY),
        "debug": config.DEBUG,
        "run": run_state.to_dict(),
    }


# ── Run / Stop ────────────────────────────────────────────────────────


class RunRequest(BaseModel):
    """Request body for POST /run."""

    goal: str = Field(..., description="Natural-language goal to accomplish.")
    context: Optional[str] = Field(
        default=None, description="Additional context or constraints."
    )
    require_confirmation: bool = Field(
        default=True,
        description="Whether destructive actions need user confirmation.",
    )


class RunResponse(BaseModel):
    """Response body for POST /run."""

    task_id: str
    status: str
    message: str


@app.post("/run", response_model=RunResponse, tags=["Execution"])
async def start_run(body: RunRequest, request: Request):
    """Start a new agent task run.

    Registers the task in the run state.  The actual step-by-step
    execution is driven by the Chrome extension which calls /plan,
    executes each step, and then calls /verify.

    Returns a task_id that can be used with /stop to cancel.
    """
    run_state: RunState = request.app.state.run_state

    if run_state.running:
        raise HTTPException(
            status_code=409,
            detail=f"A task is already running (task_id={run_state.task_id}). "
            f"POST /stop to cancel it first.",
        )

    task_id = uuid4().hex
    run_state.start(task_id, body.goal)

    logger.info("Run started — task_id=%s, goal=%s", task_id, body.goal[:120])

    return RunResponse(
        task_id=task_id,
        status="running",
        message=f"Task started. Goal: {body.goal[:200]}",
    )


@app.post("/stop", tags=["Execution"])
async def stop_run(request: Request):
    """Stop the currently running task.

    Sets the cancellation flag so the execution loop (driven by the
    extension) knows to abort.
    """
    run_state: RunState = request.app.state.run_state

    if not run_state.running:
        return {"status": "idle", "message": "No task is currently running."}

    old_id = run_state.task_id
    run_state.stop()
    logger.info("Run stopped — task_id=%s", old_id)

    return {
        "status": "stopped",
        "task_id": old_id,
        "message": "Task cancelled.",
    }


# ── CLI Entry Point ───────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    # Support .env files if python-dotenv is installed
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    cfg = load_config()
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=cfg.SERVER_PORT,
        reload=cfg.DEBUG,
        log_level="debug" if cfg.DEBUG else "info",
    )
