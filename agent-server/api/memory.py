"""
BrowserAgent — Memory API Router

GET    /memory                        →  list memories (with optional filters)
GET    /memory/{id}                   →  get a single memory item
DELETE /memory/{id}                   →  delete a memory item
PATCH  /memory/{id}                   →  update instruction or confidence of a memory item
GET    /memory/site-profiles          →  list all site profiles
DELETE /memory/site-profiles/{domain} →  delete a site profile by domain
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from schemas import MemoryItem
from memory.site_profiles import (
    get_all_profiles,
    delete_profile,
    SiteProfile,
)

logger = logging.getLogger("browseragent.api.memory")
router = APIRouter()


class MemoryUpdateBody(BaseModel):
    """Partial update payload for PATCH /memory/{id}."""

    instruction: Optional[str] = Field(
        default=None, description="New instruction text."
    )
    confidence: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="New confidence score."
    )
    success_delta: int = Field(
        default=0, description="Increment success_count by this amount."
    )
    failure_delta: int = Field(
        default=0, description="Increment failure_count by this amount."
    )


@router.get("/memory", response_model=list[MemoryItem])
async def list_memories(
    request: Request,
    type: Optional[str] = None,
    domain: Optional[str] = None,
    scope: Optional[str] = None,
):
    """List all stored memory items.

    Supports optional query-parameter filters:
      - ``type``  : episodic | semantic | site | user_rule
      - ``domain``: e.g. github.com
      - ``scope`` : global | <domain> | <url pattern>
    """
    db = request.app.state.db
    items = await db.list_memories(
        type_filter=type,
        domain_filter=domain,
        scope_filter=scope,
    )
    logger.info("Listed %d memories (type=%s, domain=%s, scope=%s)", len(items), type, domain, scope)
    return items


@router.get("/memory/{memory_id}", response_model=MemoryItem)
async def get_memory(memory_id: str, request: Request):
    """Fetch a single memory item by its ID."""
    db = request.app.state.db
    item = await db.get_memory(memory_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Memory {memory_id} not found")
    return item


@router.delete("/memory/{memory_id}")
async def delete_memory(memory_id: str, request: Request):
    """Delete a memory item by its ID.

    Also accessible via POST /memory/{id}/delete for clients that
    don't support the DELETE method.
    """
    db = request.app.state.db
    deleted = await db.delete_memory(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Memory {memory_id} not found")
    logger.info("Deleted memory %s", memory_id)
    return {"deleted": True, "memory_id": memory_id}


@router.post("/memory/{memory_id}/delete")
async def delete_memory_post(memory_id: str, request: Request):
    """POST alternative for DELETE /memory/{id}.

    Some clients (e.g. fetch from Chrome extensions) find DELETE
    awkward.  This endpoint provides the same functionality via POST.
    """
    return await delete_memory(memory_id, request)


@router.patch("/memory/{memory_id}", response_model=MemoryItem)
async def update_memory(memory_id: str, body: MemoryUpdateBody, request: Request):
    """Partially update a memory item.

    Supports changing the instruction text, confidence score, and
    incrementing success/failure counters.
    """
    db = request.app.state.db

    # Verify it exists first
    existing = await db.get_memory(memory_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Memory {memory_id} not found")

    updated = await db.update_memory(
        memory_id,
        instruction=body.instruction,
        confidence=body.confidence,
        success_delta=body.success_delta,
        failure_delta=body.failure_delta,
    )

    logger.info("Updated memory %s", memory_id)
    return updated


# ── Site Profiles ─────────────────────────────────────────────────────


@router.get("/memory/site-profiles", response_model=list[SiteProfile])
async def list_site_profiles(request: Request):
    """Return all accumulated site profiles."""
    config = request.app.state.config
    profiles = get_all_profiles(db_path=config.DB_PATH)
    logger.info("Listed %d site profiles", len(profiles))
    return profiles


@router.delete("/memory/site-profiles/{domain}")
async def delete_site_profile(domain: str, request: Request):
    """Delete the site profile for a given domain.

    Also accessible via POST for clients that don't support DELETE.
    """
    config = request.app.state.config
    try:
        delete_profile(domain=domain, db_path=config.DB_PATH)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    logger.info("Deleted site profile for %s", domain)
    return {"deleted": True, "domain": domain}


@router.post("/memory/site-profiles/{domain}/delete")
async def delete_site_profile_post(domain: str, request: Request):
    """POST alternative for DELETE /memory/site-profiles/{domain}."""
    return await delete_site_profile(domain, request)
