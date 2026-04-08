"""
BrowserAgent — Step Executor & Validator (Phase 5)

Server-side validation for individual action steps before they are
sent to the browser.  Classifies risk level, checks whether
confirmation is required, and validates step structure.

This module does NOT execute steps — that happens in the browser's
content script.  This module decides whether a step *should* be
executed and how risky it is.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from schemas import ActionStep, ActionType

logger = logging.getLogger("browseragent.executor")

# ── Sensitive action definitions ──────────────────────────────────────

# Action types that always require confirmation
_CONFIRM_ACTION_TYPES: set[ActionType] = {
    ActionType.SUBMIT,
    ActionType.NAVIGATE,
}

# Keywords in step text/reason that trigger confirmation
_SENSITIVE_KEYWORDS: set[str] = {
    "submit", "login", "log in", "sign in", "sign-in", "sign up",
    "purchase", "buy", "pay", "payment", "checkout", "check out",
    "delete", "remove", "erase", "unsubscribe",
    "cancel subscription", "close account", "deactivate",
    "confirm order", "place order", "send payment", "transfer",
    "download", "install", "authorize", "grant permission",
    "share", "publish", "post publicly",
}

# Patterns that indicate the step navigates away from the current domain
_NAVIGATE_AWAY_PATTERNS: list[re.Pattern] = [
    re.compile(r"https?://", re.I),
    re.compile(r"navigate\s+to\s+", re.I),
    re.compile(r"go\s+to\s+", re.I),
    re.compile(r"open\s+(?:url|link|page)", re.I),
]


# ── Validation ────────────────────────────────────────────────────────

def validate_step(step: ActionStep) -> dict[str, Any]:
    """Validate that an ActionStep is structurally sound.

    Checks:
      • action_type is a valid ActionType enum member
      • CLICK, TYPE, SELECT have a target (selector or text)
      • TYPE has an input_value
      • NAVIGATE has an input_value (URL)
      • SCROLL has a direction hint

    Returns:
        { valid: bool, reason: str }
    """
    # Check action type is valid
    try:
        action = ActionType(step.action_type) if isinstance(step.action_type, str) else step.action_type
    except ValueError:
        return {
            "valid": False,
            "reason": f"Unknown action_type: {step.action_type}",
        }

    # ── Target-required actions ───────────────────────────────────
    target_required = {ActionType.CLICK, ActionType.TYPE, ActionType.SELECT}
    if action in target_required:
        has_target = bool(step.target_selector or step.target_text)
        if not has_target:
            return {
                "valid": False,
                "reason": f"{action.value} requires target_selector or target_text",
            }

    # ── Value-required actions ────────────────────────────────────
    if action == ActionType.TYPE:
        if not step.input_value and step.input_value != "":
            return {
                "valid": False,
                "reason": "TYPE requires an input_value (text to type)",
            }

    if action == ActionType.NAVIGATE:
        if not step.input_value:
            return {
                "valid": False,
                "reason": "NAVIGATE requires an input_value (URL)",
            }

    # ── Scroll validation ─────────────────────────────────────────
    if action == ActionType.SCROLL:
        # input_value should contain a direction hint
        if step.input_value:
            valid_dirs = {"up", "down", "left", "right", "top", "bottom"}
            words = step.input_value.lower().split()
            if not any(w in valid_dirs for w in words):
                return {
                    "valid": False,
                    "reason": f"SCROLL input_value should contain a direction "
                              f"(up/down/left/right/top/bottom), got: {step.input_value}",
                }

    return {"valid": True, "reason": "Step is valid"}


# ── Confirmation Check ────────────────────────────────────────────────

def check_requires_confirmation(step: ActionStep) -> bool:
    """Determine whether a step should pause for user confirmation.

    Returns True if the step:
      • Is a SUBMIT or NAVIGATE action
      • Contains sensitive keywords in its text fields
      • Appears to navigate away from the current domain
    """
    action = step.action_type if isinstance(step.action_type, ActionType) else ActionType(step.action_type)

    # Always-confirm action types
    if action in _CONFIRM_ACTION_TYPES:
        return True

    # Check for sensitive keywords in all text fields
    text_fields = " ".join(filter(None, [
        step.target_text,
        step.input_value,
        step.reason,
    ])).lower()

    for keyword in _SENSITIVE_KEYWORDS:
        if keyword in text_fields:
            logger.info(
                "Step %s requires confirmation: keyword '%s' found",
                step.step_id, keyword,
            )
            return True

    # Check for navigate-away patterns
    for pattern in _NAVIGATE_AWAY_PATTERNS:
        if step.input_value and pattern.search(step.input_value):
            if action == ActionType.CLICK or action == ActionType.NAVIGATE:
                return True

    return False


# ── Risk Assessment ───────────────────────────────────────────────────

def estimate_step_risk(step: ActionStep) -> str:
    """Classify a step's risk as 'low', 'medium', or 'high'.

    Risk criteria:
      • high:   destructive actions (delete, payment, account changes)
      • medium: state-changing actions (submit, login, navigate away)
      • low:    read-only actions (scan, scroll, extract, wait)
    """
    action = step.action_type if isinstance(step.action_type, ActionType) else ActionType(step.action_type)

    # ── Read-only / passive → low risk ────────────────────────────
    low_risk_types = {
        ActionType.SCAN,
        ActionType.SCROLL,
        ActionType.EXTRACT,
        ActionType.WAIT,
        ActionType.SCREENSHOT,
        ActionType.SWITCH_TAB,
    }
    if action in low_risk_types:
        return "low"

    text_fields = " ".join(filter(None, [
        step.target_text,
        step.input_value,
        step.reason,
    ])).lower()

    # ── High-risk keywords ────────────────────────────────────────
    high_risk_keywords = {
        "delete", "remove", "erase", "destroy",
        "purchase", "buy", "pay", "payment", "checkout",
        "cancel subscription", "close account", "deactivate",
        "transfer funds", "wire", "send money",
        "authorize", "grant permission",
    }
    for kw in high_risk_keywords:
        if kw in text_fields:
            return "high"

    # ── Medium-risk: SUBMIT, NAVIGATE, LOGIN ──────────────────────
    medium_risk_types = {ActionType.SUBMIT, ActionType.NAVIGATE}
    if action in medium_risk_types:
        return "medium"

    medium_risk_keywords = {
        "submit", "login", "sign in", "sign up", "register",
        "confirm", "send", "post", "publish",
    }
    for kw in medium_risk_keywords:
        if kw in text_fields:
            return "medium"

    # ── CLICK and TYPE are low risk by default ────────────────────
    return "low"


# ── Batch validation ──────────────────────────────────────────────────

def validate_plan(steps: list[ActionStep]) -> dict[str, Any]:
    """Validate an entire plan and return an aggregate report.

    Returns:
        {
            valid: bool,
            total_steps: int,
            invalid_steps: list[{ step_id, reason }],
            risk_summary: { low: int, medium: int, high: int },
            requires_confirmation: bool,
        }
    """
    invalid: list[dict[str, str]] = []
    risk_counts = {"low": 0, "medium": 0, "high": 0}
    needs_confirm = False

    for step in steps:
        result = validate_step(step)
        if not result["valid"]:
            invalid.append({
                "step_id": step.step_id,
                "reason": result["reason"],
            })

        risk = estimate_step_risk(step)
        risk_counts[risk] = risk_counts.get(risk, 0) + 1

        if check_requires_confirmation(step):
            needs_confirm = True

    return {
        "valid": len(invalid) == 0,
        "total_steps": len(steps),
        "invalid_steps": invalid,
        "risk_summary": risk_counts,
        "requires_confirmation": needs_confirm,
    }
