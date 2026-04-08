"""Test suite for Phase 10 — Site Profiles, Run Summarizer, Learning Engine."""

import os
import sys
import tempfile
import json

sys.path.insert(0, ".")

from memory.site_profiles import (
    SiteProfile,
    save_profile,
    get_profile,
    update_profile,
    update_from_run,
    get_all_profiles,
    delete_profile,
)
from core.run_summarizer import (
    RunSummary,
    summarize_run,
    format_for_memory,
    compare_runs,
    get_domain_stats,
)
from core.learning_engine import (
    analyze_selector_patterns,
    analyze_button_patterns,
)


# ── Test Data ─────────────────────────────────────────────────────────

STEPS = [
    {"step_id": "s1", "action_type": "CLICK", "target_selector": "#option-a", "target_text": "Option A", "reason": "Select answer for question 1"},
    {"step_id": "s2", "action_type": "CLICK", "target_selector": "#option-b", "target_text": "ATP", "reason": "Select mcq answer"},
    {"step_id": "s3", "action_type": "CLICK", "target_selector": "button.next-btn", "target_text": "Next", "reason": "Navigate to next page"},
    {"step_id": "s4", "action_type": "TYPE", "target_selector": "#email-input", "input_value": "test@test.com", "reason": "Fill email"},
    {"step_id": "s5", "action_type": "CLICK", "target_selector": "button.submit", "target_text": "Submit Quiz", "reason": "Submit the form"},
]

RESULTS = [
    {"step_id": "s1", "success": True, "action_taken": "Clicked Option A"},
    {"step_id": "s2", "success": True, "action_taken": "Clicked ATP"},
    {"step_id": "s3", "success": True, "action_taken": "Clicked Next"},
    {"step_id": "s4", "success": True, "action_taken": "Typed email"},
    {"step_id": "s5", "success": False, "action_taken": "", "error": "Element not found"},
]


# ── Site Profile Tests ────────────────────────────────────────────────

def test_site_profile_crud():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        profile = SiteProfile(
            domain="quiz.example.com",
            next_button_patterns=["Next", "Continue"],
            submit_button_patterns=["Submit"],
            mcq_selectors=["input[type=radio]"],
        )

        # Save
        save_profile("quiz.example.com", profile, db_path)

        # Get
        loaded = get_profile("quiz.example.com", db_path)
        assert loaded is not None
        assert loaded.domain == "quiz.example.com"
        assert "Next" in loaded.next_button_patterns
        assert "Continue" in loaded.next_button_patterns
        assert len(loaded.mcq_selectors) == 1
        print("  ✓ save_profile + get_profile")

        # Update
        update_profile("quiz.example.com", {
            "next_button_patterns": ["Proceed"],
            "known_iframes": True,
        }, db_path)
        updated = get_profile("quiz.example.com", db_path)
        assert "Proceed" in updated.next_button_patterns
        assert "Next" in updated.next_button_patterns  # merged
        assert updated.known_iframes is True
        print("  ✓ update_profile (merge lists + overwrite scalars)")

        # Get all
        all_profiles = get_all_profiles(db_path)
        assert len(all_profiles) == 1
        print("  ✓ get_all_profiles")

        # Delete
        delete_profile("quiz.example.com", db_path)
        assert get_profile("quiz.example.com", db_path) is None
        print("  ✓ delete_profile")

    finally:
        os.unlink(db_path)


def test_update_from_run():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        # First run
        update_from_run("test.com", {
            "selectors_used": ["#opt-a", "#opt-b"],
            "buttons_clicked": ["Next", "Submit Quiz"],
            "questions_answered": 3,
            "success": True,
            "confidence": 0.8,
        }, db_path)

        profile = get_profile("test.com", db_path)
        assert profile is not None
        assert profile.total_runs == 1
        assert profile.success_rate == 1.0
        assert "#opt-a" in profile.mcq_selectors
        assert "Next" in profile.next_button_patterns
        assert "Submit Quiz" in profile.submit_button_patterns
        print("  ✓ update_from_run (first run, success)")

        # Second run (failure)
        update_from_run("test.com", {
            "selectors_used": ["#opt-c"],
            "buttons_clicked": ["Continue"],
            "success": False,
            "confidence": 0.4,
        }, db_path)

        profile = get_profile("test.com", db_path)
        assert profile.total_runs == 2
        assert profile.success_rate == 0.5
        # Failed run should NOT add new patterns
        assert "#opt-c" not in profile.mcq_selectors
        print("  ✓ update_from_run (second run, failure — patterns not added)")

    finally:
        os.unlink(db_path)


# ── Run Summarizer Tests ──────────────────────────────────────────────

def test_summarize_run():
    summary = summarize_run(
        run_id="run-001",
        goal="Answer the biology quiz",
        domain="quiz.example.com",
        steps=STEPS,
        results=RESULTS,
        start_time_ms=1000,
        end_time_ms=6000,
    )

    assert isinstance(summary, RunSummary)
    assert summary.total_steps == 5
    assert summary.successful_steps == 4
    assert summary.failed_steps == 1
    assert summary.final_status == "partial"  # 4/5 = 0.80, not > 0.80
    assert summary.duration_ms == 5000
    assert "button.next-btn" in summary.selectors_used
    assert "#option-a" in summary.selectors_used
    assert "Next" in summary.buttons_clicked
    assert summary.questions_answered >= 1
    assert len(summary.key_findings) > 0
    print(f"  ✓ summarize_run: {summary.final_status}, {summary.successful_steps}/{summary.total_steps} steps")


def test_format_for_memory():
    summary = summarize_run(
        run_id="run-001", goal="Answer the biology quiz",
        domain="quiz.example.com", steps=STEPS, results=RESULTS,
        start_time_ms=0, end_time_ms=3000,
    )
    text = format_for_memory(summary)
    assert len(text) <= 500
    assert "quiz.example.com" in text
    assert "partial" in text.lower() or "success" in text.lower()
    print(f"  ✓ format_for_memory: {len(text)} chars")


def test_compare_runs():
    run1 = RunSummary(
        run_id="r1", goal="quiz", domain="test.com",
        total_steps=10, successful_steps=6, failed_steps=4,
        avg_confidence=0.6, duration_ms=5000, questions_answered=3,
        final_status="partial",
    )
    run2 = RunSummary(
        run_id="r2", goal="quiz", domain="test.com",
        total_steps=8, successful_steps=7, failed_steps=1,
        avg_confidence=0.85, duration_ms=4000, questions_answered=5,
        final_status="success",
    )
    comparison = compare_runs(run1, run2)
    assert comparison["overall_trend"] == "improving"
    assert "success_rate" in comparison["improved_metrics"]
    assert "avg_confidence" in comparison["improved_metrics"]
    print(f"  ✓ compare_runs: {comparison['overall_trend']}, "
          f"+{len(comparison['improved_metrics'])} improved")


# ── Learning Engine Tests ─────────────────────────────────────────────

def test_analyze_selectors():
    patterns = analyze_selector_patterns(STEPS, RESULTS)
    assert "#option-a" in patterns["successful_selectors"]
    assert "button.next-btn" in patterns["successful_selectors"]
    assert "button.submit" in patterns["failed_selectors"]
    print(f"  ✓ analyze_selector_patterns: {len(patterns['successful_selectors'])} success, "
          f"{len(patterns['failed_selectors'])} failed")


def test_analyze_buttons():
    patterns = analyze_button_patterns(STEPS, RESULTS)
    assert "Next" in patterns["next_patterns"]
    # Submit Quiz would only appear if the click succeeded — it failed
    assert "Submit Quiz" not in patterns["submit_patterns"]
    print(f"  ✓ analyze_button_patterns: {len(patterns['next_patterns'])} next, "
          f"{len(patterns['submit_patterns'])} submit")


def test_domain_stats():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        # Create table and insert test data
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE task_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT UNIQUE, goal TEXT, domain TEXT,
                steps_json TEXT DEFAULT '[]', results_json TEXT DEFAULT '[]',
                success INTEGER DEFAULT 0, created_at TEXT
            );
        """)
        conn.execute(
            "INSERT INTO task_runs (task_id, goal, domain, success, created_at) VALUES (?, ?, ?, ?, ?)",
            ("t1", "answer quiz", "test.com", 1, "2026-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO task_runs (task_id, goal, domain, success, created_at) VALUES (?, ?, ?, ?, ?)",
            ("t2", "answer quiz", "test.com", 0, "2026-01-02T00:00:00"),
        )
        conn.execute(
            "INSERT INTO task_runs (task_id, goal, domain, success, created_at) VALUES (?, ?, ?, ?, ?)",
            ("t3", "fill form", "test.com", 1, "2026-01-03T00:00:00"),
        )
        conn.commit()
        conn.close()

        stats = get_domain_stats("test.com", db_path)
        assert stats["total_runs"] == 3
        assert stats["success_rate"] == round(2/3, 4)
        assert "answer quiz" in stats["most_common_goals"]
        print(f"  ✓ get_domain_stats: {stats['total_runs']} runs, "
              f"{stats['success_rate']*100:.1f}% success")

    finally:
        os.unlink(db_path)


# ── Run All ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Testing Site Profiles...")
    test_site_profile_crud()
    test_update_from_run()

    print("\nTesting Run Summarizer...")
    test_summarize_run()
    test_format_for_memory()
    test_compare_runs()

    print("\nTesting Learning Engine...")
    test_analyze_selectors()
    test_analyze_buttons()
    test_domain_stats()

    print("\n✓ All Phase 10 tests passed")
