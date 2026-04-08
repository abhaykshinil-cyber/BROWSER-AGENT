"""
BrowserAgent — Site Profiles Store (Phase 10)

Per-domain site profiles that accumulate structural knowledge about
websites the agent has interacted with: button patterns, MCQ selectors,
iframe/shadow-DOM presence, and aggregate success metrics.

Backward-compatible with the Phase 4 ``site_profiles`` table; reads
old-style rows and transparently upgrades them to the richer model.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger("browseragent.memory.site_profiles")


# ── Model ─────────────────────────────────────────────────────────────


class SiteProfile(BaseModel):
    """Accumulated structural knowledge about a website."""

    domain: str = Field(..., description="Domain this profile describes.")
    next_button_patterns: list[str] = Field(
        default_factory=list,
        description='Observed next/continue button texts, e.g. ["Next", "Continue"]',
    )
    submit_button_patterns: list[str] = Field(
        default_factory=list,
        description='Observed submit/finish button texts, e.g. ["Submit", "Finish"]',
    )
    mcq_selectors: list[str] = Field(
        default_factory=list,
        description='CSS selectors for MCQ option elements.',
    )
    custom_option_classes: list[str] = Field(
        default_factory=list,
        description='Class names for custom option/choice elements.',
    )
    page_type_hints: dict[str, str] = Field(
        default_factory=dict,
        description='Structural hints, e.g. {"quiz_indicator": ".quiz-container"}',
    )
    known_iframes: bool = Field(default=False, description="Site uses iframes.")
    known_shadow_dom: bool = Field(default=False, description="Site uses shadow DOM.")
    avg_confidence: float = Field(default=0.0, description="Average confidence across runs.")
    total_runs: int = Field(default=0, description="Total number of agent runs.")
    success_rate: float = Field(default=0.0, description="Fraction of successful runs.")
    notes: str = Field(default="", description="Free-text observations.")
    last_updated: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO timestamp of last profile update.",
    )


# ── Database Helpers ──────────────────────────────────────────────────


def _get_conn(db_path: str) -> sqlite3.Connection:
    """Open a connection with Row factory and ensure table exists."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS site_profiles (
            domain                 TEXT PRIMARY KEY,
            profile_json           TEXT NOT NULL DEFAULT '{}',
            next_button_patterns   TEXT NOT NULL DEFAULT '[]',
            submit_button_patterns TEXT NOT NULL DEFAULT '[]',
            mcq_selectors          TEXT NOT NULL DEFAULT '[]',
            custom_notes           TEXT NOT NULL DEFAULT '',
            last_updated           TEXT NOT NULL DEFAULT ''
        );
    """)
    # Add profile_json column if missing (upgrading from Phase 4)
    try:
        conn.execute("SELECT profile_json FROM site_profiles LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE site_profiles ADD COLUMN profile_json TEXT NOT NULL DEFAULT '{}'")
        conn.commit()
    conn.commit()
    return conn


def _row_to_profile(row: dict[str, Any]) -> SiteProfile:
    """Convert a DB row to a SiteProfile, handling both old and new schemas."""
    domain = row.get("domain", "")
    profile_json = row.get("profile_json", None)

    if profile_json and profile_json != "{}":
        try:
            obj = json.loads(profile_json)
            obj["domain"] = domain
            return SiteProfile(**obj)
        except Exception:
            pass

    # Fallback: reconstruct from old columns
    return SiteProfile(
        domain=domain,
        next_button_patterns=json.loads(row.get("next_button_patterns", "[]") or "[]"),
        submit_button_patterns=json.loads(row.get("submit_button_patterns", "[]") or "[]"),
        mcq_selectors=json.loads(row.get("mcq_selectors", "[]") or "[]"),
        notes=row.get("custom_notes", "") or "",
        last_updated=row.get("last_updated", "") or "",
    )


# ── CRUD Operations ──────────────────────────────────────────────────


def save_profile(domain: str, profile: SiteProfile, db_path: str) -> None:
    """Upsert a site profile.

    Args:
        domain:  The domain key.
        profile: Full SiteProfile to persist.
        db_path: Path to the SQLite database.
    """
    profile.last_updated = datetime.now(timezone.utc).isoformat()
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """INSERT OR REPLACE INTO site_profiles
               (domain, profile_json, next_button_patterns, submit_button_patterns,
                mcq_selectors, custom_notes, last_updated)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                domain,
                profile.model_dump_json(),
                json.dumps(profile.next_button_patterns),
                json.dumps(profile.submit_button_patterns),
                json.dumps(profile.mcq_selectors),
                profile.notes,
                profile.last_updated,
            ),
        )
        conn.commit()
        logger.info("Saved site profile for %s", domain)
    finally:
        conn.close()


def get_profile(domain: str, db_path: str) -> Optional[SiteProfile]:
    """Retrieve a site profile by domain.

    Returns:
        SiteProfile if found, None otherwise.
    """
    conn = _get_conn(db_path)
    try:
        cursor = conn.execute(
            "SELECT * FROM site_profiles WHERE domain = ?", (domain,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return _row_to_profile(dict(row))
    finally:
        conn.close()


def update_profile(domain: str, updates: dict[str, Any], db_path: str) -> None:
    """Merge updates into an existing profile.

    For list fields: extends with new items (deduplicated).
    For scalar fields: overwrites with new value.
    """
    existing = get_profile(domain, db_path)
    if existing is None:
        existing = SiteProfile(domain=domain)

    data = existing.model_dump()
    _LIST_FIELDS = {
        "next_button_patterns", "submit_button_patterns",
        "mcq_selectors", "custom_option_classes",
    }

    for key, value in updates.items():
        if key in _LIST_FIELDS and isinstance(value, list):
            current = data.get(key, [])
            merged = list(dict.fromkeys(current + value))
            data[key] = merged
        elif key == "page_type_hints" and isinstance(value, dict):
            current = data.get("page_type_hints", {})
            current.update(value)
            data[key] = current
        elif key in data:
            data[key] = value

    updated = SiteProfile(**data)
    save_profile(domain, updated, db_path)


def update_from_run(
    domain: str, run_results: dict[str, Any], db_path: str
) -> None:
    """Update profile based on a completed run's outcome.

    Args:
        domain:      The website domain.
        run_results: Dict with keys: selectors_used, buttons_clicked,
                     questions_answered, success, confidence.
        db_path:     Path to the SQLite database.
    """
    existing = get_profile(domain, db_path)
    if existing is None:
        existing = SiteProfile(domain=domain)

    success = run_results.get("success", False)
    selectors = run_results.get("selectors_used", [])
    buttons = run_results.get("buttons_clicked", [])
    confidence = run_results.get("confidence", 0.5)

    # Run stats
    existing.total_runs += 1
    old_successes = round(existing.success_rate * (existing.total_runs - 1))
    new_successes = old_successes + (1 if success else 0)
    existing.success_rate = round(
        new_successes / existing.total_runs, 4
    ) if existing.total_runs > 0 else 0.0

    if existing.avg_confidence == 0.0:
        existing.avg_confidence = confidence
    else:
        existing.avg_confidence = round(
            (existing.avg_confidence * (existing.total_runs - 1) + confidence)
            / existing.total_runs, 4,
        )

    # Only add patterns from successful runs
    if success:
        if selectors:
            existing.mcq_selectors = list(
                dict.fromkeys(existing.mcq_selectors + selectors)
            )

        _NEXT = {"next", "continue", "proceed", "forward", "go"}
        _SUBMIT = {"submit", "finish", "done", "complete", "send", "grade", "check", "save"}

        for btn in buttons:
            words = set(btn.lower().strip().split())
            if words & _NEXT:
                if btn not in existing.next_button_patterns:
                    existing.next_button_patterns.append(btn)
            elif words & _SUBMIT:
                if btn not in existing.submit_button_patterns:
                    existing.submit_button_patterns.append(btn)

    save_profile(domain, existing, db_path)


def get_all_profiles(db_path: str) -> list[SiteProfile]:
    """Retrieve all site profiles sorted by total_runs descending."""
    conn = _get_conn(db_path)
    try:
        cursor = conn.execute("SELECT * FROM site_profiles ORDER BY last_updated DESC")
        rows = cursor.fetchall()
        profiles = [_row_to_profile(dict(r)) for r in rows]
        profiles.sort(key=lambda p: p.total_runs, reverse=True)
        return profiles
    finally:
        conn.close()


def delete_profile(domain: str, db_path: str) -> None:
    """Remove a site profile."""
    conn = _get_conn(db_path)
    try:
        conn.execute("DELETE FROM site_profiles WHERE domain = ?", (domain,))
        conn.commit()
        logger.info("Deleted site profile for %s", domain)
    finally:
        conn.close()
