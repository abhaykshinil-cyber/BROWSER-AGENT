"""
BrowserAgent — FastAPI Application Entry Point

Boots the HTTP + WebSocket server that the Chrome extension connects to.
Exposes REST endpoints for health checks and WebSocket endpoints for
real-time bidirectional communication with the extension.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from config import AgentConfig, load_config
from schemas import (
    ActionResult,
    AgentTask,
    PageContext,
    PlanRequest,
    PlanResponse,
    VerifyRequest,
    VerifyResponse,
)

# ── Logging ───────────────────────────────────────────────────────────

logger = logging.getLogger("browseragent")


# ── Application State ─────────────────────────────────────────────────

class AppState:
    """Mutable runtime state shared across the application."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.active_connections: dict[str, WebSocket] = {}
        self.current_task: AgentTask | None = None
        self.is_executing: bool = False

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send a JSON message to every connected WebSocket client."""
        dead: list[str] = []
        for conn_id, ws in self.active_connections.items():
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(conn_id)
        for conn_id in dead:
            self.active_connections.pop(conn_id, None)


# ── Lifespan ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hook for the FastAPI application."""
    config = load_config()
    app.state.agent = AppState(config)

    log_level = logging.DEBUG if config.DEBUG else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )
    logger.info(
        "BrowserAgent server starting on port %d (debug=%s)",
        config.SERVER_PORT,
        config.DEBUG,
    )
    logger.info("LLM model: %s", config.MODEL)
    logger.info("Database:  %s", config.DB_PATH)

    yield

    logger.info("BrowserAgent server shutting down")
    for ws in list(app.state.agent.active_connections.values()):
        await ws.close()


# ── App Factory ───────────────────────────────────────────────────────

app = FastAPI(
    title="BrowserAgent Server",
    version="0.1.0",
    description="AI browser agent backend — planning, execution, verification, and memory.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helpers ───────────────────────────────────────────────────────────

def _envelope(msg_type: str, payload: Any, request_id: str | None = None) -> dict:
    """Wrap a payload in the standard message envelope."""
    return {
        "type": msg_type,
        "payload": payload if isinstance(payload, dict) else payload,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id or uuid4().hex,
    }


# ── REST Endpoints ────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    """Lightweight health probe for readiness checks."""
    state: AppState = app.state.agent
    return {
        "status": "ok",
        "connections": len(state.active_connections),
        "executing": state.is_executing,
        "model": state.config.MODEL,
    }


@app.post("/plan", response_model=PlanResponse)
async def create_plan(request: PlanRequest):
    """Generate an action plan for the given task and page context.

    In the full implementation this calls the LLM planner.
    Currently returns a stub so the REST contract is exercisable.
    """
    logger.info("Plan requested for goal: %s", request.task.goal)
    return PlanResponse(
        plan=[],
        reasoning="Planner module not yet wired — returning empty plan.",
        confidence=0.0,
        requires_confirmation=True,
    )


@app.post("/verify", response_model=VerifyResponse)
async def verify_result(request: VerifyRequest):
    """Verify whether the executed plan achieved the task goal.

    In the full implementation this calls the LLM verifier.
    Currently returns a stub so the REST contract is exercisable.
    """
    logger.info("Verification requested for task: %s", request.task.task_id)
    return VerifyResponse(
        success=False,
        confidence=0.0,
        summary="Verifier module not yet wired.",
        issues=["Verification logic pending implementation."],
    )


# ── WebSocket Endpoint ────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Persistent bidirectional channel between the extension and server.

    Protocol:
        1. Extension connects and sends an AGENT_INIT message.
        2. Server acknowledges with SERVER_CONNECTED.
        3. Both sides exchange messages using the types defined in
           ``shared/message-types.js``.
    """
    await websocket.accept()
    conn_id = uuid4().hex
    state: AppState = app.state.agent
    state.active_connections[conn_id] = websocket

    logger.info("WebSocket connected: %s (total: %d)", conn_id, len(state.active_connections))

    try:
        await websocket.send_json(
            _envelope("SERVER_CONNECTED", {"connection_id": conn_id})
        )

        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json(
                    _envelope("ERROR", {"detail": "Invalid JSON"})
                )
                continue

            msg_type = message.get("type", "UNKNOWN")
            payload = message.get("payload", {})
            request_id = message.get("request_id")

            logger.debug("Received [%s] from %s", msg_type, conn_id)

            await _handle_message(websocket, state, msg_type, payload, request_id)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: %s", conn_id)
    except Exception as exc:
        logger.exception("WebSocket error on %s: %s", conn_id, exc)
    finally:
        state.active_connections.pop(conn_id, None)
        logger.info("Connections remaining: %d", len(state.active_connections))


async def _handle_message(
    ws: WebSocket,
    state: AppState,
    msg_type: str,
    payload: dict[str, Any],
    request_id: str | None,
) -> None:
    """Route an incoming WebSocket message to the appropriate handler."""

    if msg_type == "PAGE_CONTEXT":
        context = PageContext(**payload)
        logger.info("Page context received: %s", context.url)
        await ws.send_json(
            _envelope("SCAN_RESULT", {"status": "received"}, request_id)
        )

    elif msg_type == "TASK_START":
        task = AgentTask(**payload)
        state.current_task = task
        state.is_executing = True
        logger.info("Task started: %s — %s", task.task_id, task.goal)
        await ws.send_json(
            _envelope("TASK_ACCEPTED", {"task_id": task.task_id}, request_id)
        )

    elif msg_type == "TASK_CANCEL":
        if state.current_task:
            logger.info("Task cancelled: %s", state.current_task.task_id)
        state.current_task = None
        state.is_executing = False
        await ws.send_json(
            _envelope("TASK_COMPLETE", {"cancelled": True}, request_id)
        )

    elif msg_type == "ACTION_RESULT":
        result = ActionResult(**payload)
        logger.info(
            "Action result for step %s: success=%s", result.step_id, result.success
        )

    elif msg_type == "TEACHING_SUBMIT":
        logger.info("Teaching received: %s", payload.get("raw_text", ""))
        await ws.send_json(
            _envelope("TEACHING_ACK", {"stored": True}, request_id)
        )

    elif msg_type == "STATUS_REQUEST":
        await ws.send_json(
            _envelope(
                "STATUS_RESPONSE",
                {
                    "executing": state.is_executing,
                    "task_id": state.current_task.task_id if state.current_task else None,
                    "model": state.config.MODEL,
                },
                request_id,
            )
        )

    else:
        logger.warning("Unknown message type: %s", msg_type)
        await ws.send_json(
            _envelope("ERROR", {"detail": f"Unknown message type: {msg_type}"}, request_id)
        )


# ── CLI Entry Point ───────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    cfg = load_config()
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=cfg.SERVER_PORT,
        reload=cfg.DEBUG,
        log_level="debug" if cfg.DEBUG else "info",
    )
