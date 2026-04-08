"""
BrowserAgent Pydantic Schema Definitions

Canonical data models shared between the FastAPI server, the planning
engine, the memory sub-system, and the Chrome extension (via JSON
serialisation).  Every model is strict—extra fields are rejected—and
carries full docstrings so auto-generated OpenAPI docs stay useful.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────


class ActionType(str, Enum):
    """Finite set of browser actions the agent can perform."""

    SCAN = "SCAN"
    SELECT = "SELECT"
    CLICK = "CLICK"
    TYPE = "TYPE"
    SCROLL = "SCROLL"
    NAVIGATE = "NAVIGATE"
    EXTRACT = "EXTRACT"
    SCREENSHOT = "SCREENSHOT"
    SUBMIT = "SUBMIT"
    WAIT = "WAIT"
    SWITCH_TAB = "SWITCH_TAB"


class MemoryType(str, Enum):
    """Categories of agent memory."""

    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    SITE = "site"
    USER_RULE = "user_rule"


# ── Page & Browser Context ────────────────────────────────────────────


class VisibleElement(BaseModel):
    """A single interactive or informational element visible in the viewport."""

    tag: str = Field(..., description="HTML tag name (e.g. 'button', 'a', 'input').")
    selector: str = Field(
        ..., description="CSS selector that uniquely identifies this element."
    )
    text: str = Field(
        default="", description="Visible inner text of the element."
    )
    attributes: dict[str, str] = Field(
        default_factory=dict,
        description="Key HTML attributes (id, class, href, type, etc.).",
    )
    bounding_box: Optional[dict[str, float]] = Field(
        default=None,
        description="Viewport-relative bounding box {x, y, width, height}.",
    )
    is_interactive: bool = Field(
        default=False,
        description="Whether the element accepts user interaction.",
    )


class TabInfo(BaseModel):
    """Metadata for a single browser tab."""

    tab_id: int = Field(..., description="Chrome tab identifier.")
    url: str = Field(..., description="Full URL of the tab.")
    title: str = Field(default="", description="Document title of the tab.")
    active: bool = Field(
        default=False, description="Whether this tab is currently focused."
    )


class PageContext(BaseModel):
    """Complete snapshot of the currently observed page and browser state.

    Sent from the content script to the agent server so the planner has
    full situational awareness before deciding on the next action.
    """

    url: str = Field(..., description="Full URL of the page being observed.")
    title: str = Field(default="", description="Document title.")
    body_text: str = Field(
        default="",
        description=(
            "Trimmed visible text content of the page body, capped to a "
            "reasonable token budget to stay within LLM context limits."
        ),
    )
    visible_elements: list[VisibleElement] = Field(
        default_factory=list,
        description="Interactive and notable elements currently in the viewport.",
    )
    screenshot_base64: Optional[str] = Field(
        default=None,
        description="Base-64 encoded PNG screenshot of the visible viewport.",
    )
    tabs: list[TabInfo] = Field(
        default_factory=list,
        description="List of all open browser tabs and their metadata.",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "url": "https://example.com/dashboard",
                "title": "Dashboard — Example App",
                "body_text": "Welcome back, Alice. You have 3 notifications.",
                "visible_elements": [],
                "screenshot_base64": None,
                "tabs": [
                    {
                        "tab_id": 1,
                        "url": "https://example.com/dashboard",
                        "title": "Dashboard — Example App",
                        "active": True,
                    }
                ],
            }
        }


# ── Task & Action Models ─────────────────────────────────────────────


class TaskSettings(BaseModel):
    """Per-task behavioural overrides."""

    max_steps: int = Field(
        default=20,
        description="Maximum number of action steps before the agent stops.",
    )
    require_confirmation: bool = Field(
        default=False,
        description="If True, pause before each destructive action and ask the user.",
    )
    allowed_domains: list[str] = Field(
        default_factory=list,
        description="If non-empty, restrict navigation to these domains only.",
    )
    viewport_only: bool = Field(
        default=True,
        description="Only interact with elements currently in the viewport.",
    )


class AgentTask(BaseModel):
    """A high-level goal the user has instructed the agent to accomplish.

    The planner decomposes this into a sequence of ``ActionStep`` objects.
    """

    task_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        description="Unique identifier for this task instance.",
    )
    goal: str = Field(
        ...,
        description="Natural-language description of what the user wants done.",
    )
    context: Optional[str] = Field(
        default=None,
        description="Additional context or constraints provided by the user.",
    )
    memory_rules: list[str] = Field(
        default_factory=list,
        description=(
            "IDs of MemoryItem rules the agent should respect while "
            "executing this task."
        ),
    )
    settings: TaskSettings = Field(
        default_factory=TaskSettings,
        description="Behavioural overrides for this specific task.",
    )


class ActionStep(BaseModel):
    """A single atomic browser action within a plan.

    Each step maps to exactly one interaction the executor will perform
    on the page (click, type, navigate, etc.).
    """

    step_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        description="Unique identifier for this step.",
    )
    action_type: ActionType = Field(
        ..., description="The type of browser action to perform."
    )
    target_selector: Optional[str] = Field(
        default=None,
        description="CSS selector for the target element (if applicable).",
    )
    target_text: Optional[str] = Field(
        default=None,
        description=(
            "Visible text of the target element, used as a fallback when "
            "the selector is ambiguous or fragile."
        ),
    )
    input_value: Optional[str] = Field(
        default=None,
        description="Text to type, URL to navigate to, or other string payload.",
    )
    reason: str = Field(
        default="",
        description="Human-readable explanation of why this action is needed.",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Planner's confidence that this step will succeed (0–1).",
    )


class ActionResult(BaseModel):
    """Outcome produced by the executor after attempting an ActionStep.

    Fed back to the planner and stored in episodic memory so the agent
    can learn from successes and failures.
    """

    step_id: str = Field(
        ..., description="ID of the ActionStep this result corresponds to."
    )
    success: bool = Field(
        ..., description="Whether the action completed without error."
    )
    action_taken: str = Field(
        default="",
        description="Human-readable summary of what the executor actually did.",
    )
    page_changed: bool = Field(
        default=False,
        description="Whether the page DOM / URL mutated as a result of the action.",
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message if the action failed.",
    )
    before_state: Optional[dict[str, Any]] = Field(
        default=None,
        description="Snapshot of relevant page state before the action executed.",
    )
    after_state: Optional[dict[str, Any]] = Field(
        default=None,
        description="Snapshot of relevant page state after the action executed.",
    )


# ── Memory & Teaching Models ─────────────────────────────────────────


class MemoryItem(BaseModel):
    """A single piece of the agent's long-term memory.

    Memory items are retrieved via vector similarity search and injected
    into the planner's prompt so the agent can recall past experiences,
    site-specific rules, and user preferences.
    """

    memory_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        description="Unique identifier for this memory entry.",
    )
    type: MemoryType = Field(
        ...,
        description=(
            "Category of memory: episodic (past experience), semantic "
            "(general knowledge), site (site-specific rule), or user_rule "
            "(explicit user instruction)."
        ),
    )
    scope: str = Field(
        default="global",
        description=(
            "Applicability scope — 'global', a domain, or a URL pattern."
        ),
    )
    domain: Optional[str] = Field(
        default=None,
        description="Domain this memory pertains to (e.g. 'github.com').",
    )
    instruction: str = Field(
        ...,
        description="The core instruction or knowledge stored in this memory.",
    )
    trigger_conditions: list[str] = Field(
        default_factory=list,
        description=(
            "Conditions under which this memory should be activated "
            "(e.g. 'user is on settings page', 'form has captcha')."
        ),
    )
    preferred_actions: list[str] = Field(
        default_factory=list,
        description="Actions the agent should prefer when this memory fires.",
    )
    avoid_actions: list[str] = Field(
        default_factory=list,
        description="Actions the agent should avoid when this memory fires.",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Bayesian confidence score, updated with each use.",
    )
    success_count: int = Field(
        default=0, ge=0, description="How many times applying this memory led to success."
    )
    failure_count: int = Field(
        default=0, ge=0, description="How many times applying this memory led to failure."
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the memory was first created.",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of the most recent update.",
    )


class TeachingPrompt(BaseModel):
    """Structured representation of a user's teaching instruction.

    When a user says *"On GitHub, always use the keyboard shortcut to
    search"*, the intake layer parses it into this schema so the memory
    sub-system can persist it as a ``MemoryItem``.
    """

    raw_text: str = Field(
        ..., description="The user's original instruction verbatim."
    )
    task_type: Optional[str] = Field(
        default=None,
        description="Category of task this teaching applies to (e.g. 'search', 'login').",
    )
    scope: str = Field(
        default="global",
        description="Applicability scope — 'global', a domain, or a URL pattern.",
    )
    domain: Optional[str] = Field(
        default=None,
        description="Domain constraint (e.g. 'github.com').",
    )
    trigger: Optional[str] = Field(
        default=None,
        description="Condition that should activate this rule.",
    )
    preferred_behavior: Optional[str] = Field(
        default=None,
        description="What the agent should do when the rule fires.",
    )
    avoid_behavior: Optional[str] = Field(
        default=None,
        description="What the agent should NOT do when the rule fires.",
    )
    priority: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Priority of this teaching (1 = low, 10 = critical).",
    )


# ── Planner Request / Response ────────────────────────────────────────


class PlanRequest(BaseModel):
    """Payload sent to the planning endpoint.

    Bundles the user's task, the current page context, and any relevant
    long-term memories that the retrieval layer surfaced.
    """

    task: AgentTask = Field(
        ..., description="The high-level task to generate a plan for."
    )
    page_context: PageContext = Field(
        ..., description="Current state of the observed page / browser."
    )
    relevant_memories: list[MemoryItem] = Field(
        default_factory=list,
        description="Memory items retrieved for this task + page combination.",
    )


class PlanResponse(BaseModel):
    """Payload returned by the planning endpoint.

    Contains an ordered list of action steps the executor should perform,
    along with the planner's reasoning and confidence assessment.
    """

    plan: list[ActionStep] = Field(
        ..., description="Ordered sequence of actions to execute."
    )
    reasoning: str = Field(
        default="",
        description="Chain-of-thought explanation of the plan.",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Overall confidence that this plan achieves the goal.",
    )
    requires_confirmation: bool = Field(
        default=False,
        description=(
            "If True, the executor should pause and ask the user for "
            "confirmation before running the plan."
        ),
    )


# ── Verification Request / Response ──────────────────────────────────


class VerifyRequest(BaseModel):
    """Payload sent to the verification endpoint after executing a plan.

    The verifier compares ``expected_outcome`` against ``actual_result``
    and the new page context to decide whether the task succeeded.
    """

    task: AgentTask = Field(
        ..., description="The original task that was being executed."
    )
    steps_executed: list[ActionResult] = Field(
        ..., description="Results of every action step that was executed."
    )
    page_context: PageContext = Field(
        ..., description="Page state after executing the plan."
    )
    expected_outcome: Optional[str] = Field(
        default=None,
        description="Natural-language description of the desired end state.",
    )


class VerifyResponse(BaseModel):
    """Payload returned by the verification endpoint.

    Tells the orchestrator whether the task succeeded, partially
    succeeded, or failed, and optionally suggests a corrective plan.
    """

    success: bool = Field(
        ..., description="Whether the task goal has been achieved."
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Verifier's confidence in its assessment.",
    )
    summary: str = Field(
        default="",
        description="Human-readable summary of the verification outcome.",
    )
    issues: list[str] = Field(
        default_factory=list,
        description="List of issues or discrepancies found.",
    )
    suggested_retry_plan: Optional[list[ActionStep]] = Field(
        default=None,
        description="If the task failed, an optional corrective plan.",
    )
