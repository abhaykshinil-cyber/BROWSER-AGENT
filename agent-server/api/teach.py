"""
BrowserAgent — Teaching API Router (Phase 7)

Full CRUD for the teaching/rule system:
  POST   /teach           — parse, validate, dedup, save
  GET    /teach/preview   — parse without saving
  DELETE /teach/{id}      — delete a rule
  PATCH  /teach/{id}      — partial update
  GET    /teach/all       — list all user rules
  GET    /teach/conflicts — scan for conflicting rules
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from schemas import MemoryItem, MemoryType, TeachingPrompt
from core.policy_engine import (
    parse_teaching_prompt,
    validate_memory_item,
)
from memory.rule_merger import (
    find_similar_rules,
    are_conflicting,
)

logger = logging.getLogger("browseragent.api.teach")
router = APIRouter()


# ── Request / Response Models ─────────────────────────────────────────


class TeachResponse(BaseModel):
    """Response for POST /teach."""
    memory_item: MemoryItem
    warnings: list[str] = Field(default_factory=list)
    similar_rules: list[MemoryItem] = Field(default_factory=list)
    saved: bool = False


class PreviewResponse(BaseModel):
    """Response for GET /teach/preview."""
    parsed: MemoryItem
    validation: dict[str, Any]
    similar_rules: list[MemoryItem] = Field(default_factory=list)


class PatchRequest(BaseModel):
    """Request body for PATCH /teach/{memory_id}."""
    instruction: Optional[str] = None
    confidence: Optional[float] = None
    preferred_actions: Optional[list[str]] = None
    avoid_actions: Optional[list[str]] = None
    trigger_conditions: Optional[list[str]] = None
    scope: Optional[str] = None
    domain: Optional[str] = None


class AllRulesResponse(BaseModel):
    """Response for GET /teach/all."""
    rules: list[MemoryItem]
    total: int


class ConflictItem(BaseModel):
    """A single conflict between two rules."""
    rule_a: MemoryItem
    rule_b: MemoryItem
    reason: str


class ConflictsResponse(BaseModel):
    """Response for GET /teach/conflicts."""
    conflicts: list[ConflictItem]


# ── POST /teach ───────────────────────────────────────────────────────


@router.post("/teach", response_model=TeachResponse)
async def submit_teaching(body: TeachingPrompt, request: Request):
    """Accept a natural-language teaching instruction from the user.

    Pipeline:
      1. Parse the instruction into a MemoryItem via Claude (or fallback).
      2. Validate the parsed item for structural correctness.
      3. Check for similar existing rules (deduplication).
      4. If a very similar rule exists (similarity > 0.8),
         return a warning instead of saving a duplicate.
      5. Otherwise, persist the new rule.
    """
    config = request.app.state.config
    db = request.app.state.db

    logger.info("Teaching received: %s", body.raw_text[:120])

    # 1. Parse
    memory_item = await parse_teaching_prompt(
        raw_text=body.raw_text,
        api_key=config.API_KEY,
        model=config.MODEL,
        domain=body.domain,
        scope=body.scope,
    )

    # 2. Validate
    validation = validate_memory_item(memory_item)
    warnings: list[str] = list(validation.get("warnings", []))

    if not validation["valid"]:
        logger.warning("Teaching validation failed: %s", warnings)
        return TeachResponse(
            memory_item=memory_item,
            warnings=warnings,
            similar_rules=[],
            saved=False,
        )

    # 3. Check for duplicates
    existing_rules = await db.list_memories(type_filter="user_rule")
    # Also check site rules
    site_rules = await db.list_memories(type_filter="site")
    all_existing = existing_rules + site_rules

    similar = find_similar_rules(memory_item, all_existing, threshold=0.6)

    # 4. Block near-exact duplicates
    very_similar = find_similar_rules(memory_item, all_existing, threshold=0.8)
    if very_similar:
        existing = very_similar[0]
        warnings.append(
            f"A very similar rule already exists (ID: {existing.memory_id}): "
            f'"{existing.instruction[:100]}"'
        )
        logger.info(
            "Duplicate detected — not saving. Existing: %s", existing.memory_id
        )
        return TeachResponse(
            memory_item=memory_item,
            warnings=warnings,
            similar_rules=very_similar[:3],
            saved=False,
        )

    # 5. Save
    saved = await db.save_memory(memory_item)
    logger.info(
        "Teaching saved as memory %s (type=%s, scope=%s)",
        saved.memory_id,
        saved.type.value if isinstance(saved.type, MemoryType) else saved.type,
        saved.scope,
    )

    if similar:
        warnings.append(
            f"Found {len(similar)} similar rule(s) — consider merging."
        )

    return TeachResponse(
        memory_item=saved,
        warnings=warnings,
        similar_rules=similar[:3],
        saved=True,
    )


# ── GET /teach/preview ────────────────────────────────────────────────


@router.get("/teach/preview", response_model=PreviewResponse)
async def preview_teaching(
    request: Request,
    text: str = Query(..., description="Raw teaching instruction to preview"),
    domain: Optional[str] = Query(None, description="Domain context"),
    scope: Optional[str] = Query(None, description="Scope override"),
):
    """Parse a teaching instruction without saving it.

    Returns the parsed MemoryItem, validation result, and similar
    existing rules so the user can review before committing.
    """
    config = request.app.state.config
    db = request.app.state.db

    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="'text' query parameter is required")

    # Parse
    memory_item = await parse_teaching_prompt(
        raw_text=text.strip(),
        api_key=config.API_KEY,
        model=config.MODEL,
        domain=domain,
        scope=scope,
    )

    # Validate
    validation = validate_memory_item(memory_item)

    # Find similar rules
    existing_rules = await db.list_memories(type_filter="user_rule")
    site_rules = await db.list_memories(type_filter="site")
    similar = find_similar_rules(memory_item, existing_rules + site_rules, threshold=0.5)

    return PreviewResponse(
        parsed=memory_item,
        validation=validation,
        similar_rules=similar[:5],
    )


# ── DELETE /teach/{memory_id} ─────────────────────────────────────────


@router.delete("/teach/{memory_id}")
async def delete_teaching(memory_id: str, request: Request):
    """Delete a rule from the store by memory_id."""
    db = request.app.state.db

    deleted = await db.delete_memory(memory_id)

    if not deleted:
        raise HTTPException(status_code=404, detail=f"Rule {memory_id} not found")

    logger.info("Deleted rule %s", memory_id)
    return {"deleted": True, "memory_id": memory_id}


# ── PATCH /teach/{memory_id} ──────────────────────────────────────────


@router.patch("/teach/{memory_id}", response_model=MemoryItem)
async def patch_teaching(memory_id: str, body: PatchRequest, request: Request):
    """Partially update a rule's fields.

    Only the fields provided in the request body are updated.
    Returns the updated MemoryItem.
    """
    db = request.app.state.db

    # Check the rule exists
    existing = await db.get_memory(memory_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Rule {memory_id} not found")

    # Build update dict from non-None fields
    updates: dict[str, Any] = {}
    if body.instruction is not None:
        updates["instruction"] = body.instruction
    if body.confidence is not None:
        if body.confidence < 0.0 or body.confidence > 1.0:
            raise HTTPException(
                status_code=400,
                detail="Confidence must be between 0.0 and 1.0",
            )
        updates["confidence"] = body.confidence
    if body.preferred_actions is not None:
        updates["preferred_actions"] = body.preferred_actions
    if body.avoid_actions is not None:
        updates["avoid_actions"] = body.avoid_actions
    if body.trigger_conditions is not None:
        updates["trigger_conditions"] = body.trigger_conditions
    if body.scope is not None:
        updates["scope"] = body.scope
    if body.domain is not None:
        updates["domain"] = body.domain

    if not updates:
        return existing

    # The db.update_memory only supports instruction and confidence directly.
    # For full field updates, we modify and re-save the entire item.
    import json
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()

    # Build SET clause dynamically
    sets: list[str] = ["updated_at = ?"]
    params: list[Any] = [now]

    simple_fields = {"instruction", "confidence", "scope", "domain"}
    json_fields = {
        "preferred_actions": "preferred_actions",
        "avoid_actions": "avoid_actions",
        "trigger_conditions": "trigger_conditions",
    }

    for field_name in simple_fields:
        if field_name in updates:
            sets.append(f"{field_name} = ?")
            params.append(updates[field_name])

    for field_name, col_name in json_fields.items():
        if field_name in updates:
            sets.append(f"{col_name} = ?")
            params.append(json.dumps(updates[field_name]))

    params.append(memory_id)

    await request.app.state.db._conn.execute(
        f"UPDATE memories SET {', '.join(sets)} WHERE memory_id = ?",
        params,
    )
    await request.app.state.db._conn.commit()

    # Return updated item
    updated = await db.get_memory(memory_id)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to retrieve updated rule")

    logger.info("Patched rule %s — fields: %s", memory_id, list(updates.keys()))
    return updated


# ── GET /teach/all ────────────────────────────────────────────────────


@router.get("/teach/all", response_model=AllRulesResponse)
async def list_all_rules(
    request: Request,
    domain: Optional[str] = Query(None, description="Filter by domain"),
    scope: Optional[str] = Query(None, description="Filter by scope"),
):
    """List all user_rule type memory items.

    Optional filters by domain and scope.
    """
    db = request.app.state.db

    # Fetch user_rule items
    rules = await db.list_memories(
        type_filter="user_rule",
        domain_filter=domain,
        scope_filter=scope,
    )

    # Also include site rules (they're user-created too)
    site_rules = await db.list_memories(
        type_filter="site",
        domain_filter=domain,
        scope_filter=scope,
    )

    all_rules = rules + site_rules
    # Sort by updated_at descending (most recently edited first)
    all_rules.sort(key=lambda r: r.updated_at, reverse=True)

    return AllRulesResponse(rules=all_rules, total=len(all_rules))


# ── GET /teach/conflicts ─────────────────────────────────────────────


@router.get("/teach/conflicts", response_model=ConflictsResponse)
async def find_conflicts(request: Request):
    """Scan all rules for conflicts.

    Returns pairs of rules that contradict each other, along with
    a human-readable reason string.
    """
    db = request.app.state.db

    # Fetch all rules
    all_rules = await db.list_memories()

    conflicts: list[ConflictItem] = []
    seen: set[tuple[str, str]] = set()

    for i, rule_a in enumerate(all_rules):
        for j, rule_b in enumerate(all_rules):
            if i >= j:
                continue

            pair_key = (rule_a.memory_id, rule_b.memory_id)
            if pair_key in seen:
                continue
            seen.add(pair_key)

            if are_conflicting(rule_a, rule_b):
                reason = _describe_conflict(rule_a, rule_b)
                conflicts.append(ConflictItem(
                    rule_a=rule_a,
                    rule_b=rule_b,
                    reason=reason,
                ))

    logger.info("Conflict scan: %d rules → %d conflicts", len(all_rules), len(conflicts))
    return ConflictsResponse(conflicts=conflicts)


def _describe_conflict(rule_a: MemoryItem, rule_b: MemoryItem) -> str:
    """Generate a human-readable description of why two rules conflict."""
    reasons: list[str] = []

    # Check A's preferred vs B's avoid
    a_pref_text = " ".join(rule_a.preferred_actions).lower()
    b_avoid_text = " ".join(rule_b.avoid_actions).lower()
    if a_pref_text and b_avoid_text:
        a_words = set(a_pref_text.split())
        b_words = set(b_avoid_text.split())
        overlap = a_words & b_words
        if len(overlap) >= 2:
            reasons.append(
                f"Rule A recommends actions that Rule B explicitly avoids "
                f"(shared keywords: {', '.join(list(overlap)[:4])})"
            )

    # Check B's preferred vs A's avoid
    b_pref_text = " ".join(rule_b.preferred_actions).lower()
    a_avoid_text = " ".join(rule_a.avoid_actions).lower()
    if b_pref_text and a_avoid_text:
        b_words = set(b_pref_text.split())
        a_words = set(a_avoid_text.split())
        overlap = b_words & a_words
        if len(overlap) >= 2:
            reasons.append(
                f"Rule B recommends actions that Rule A explicitly avoids "
                f"(shared keywords: {', '.join(list(overlap)[:4])})"
            )

    if not reasons:
        reasons.append(
            "Rules have similar instructions but contradictory preferred actions"
        )

    return "; ".join(reasons)
