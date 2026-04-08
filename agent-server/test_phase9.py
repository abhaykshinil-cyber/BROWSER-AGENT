"""Test suite for Phase 9 — Multi-Tab Context Builder."""

import sys
sys.path.insert(0, ".")

from core.tab_context_builder import (
    MultiTabContext,
    build_multi_tab_context,
    extract_relevant_facts,
    summarize_tabs,
    format_for_planner,
)


# ── Test Data ─────────────────────────────────────────────────────────

TABS = [
    {
        "tabId": 1,
        "title": "Quiz - Chapter 5 Biology",
        "url": "https://quiz.example.com/chapter5",
        "active": True,
        "is_active": True,
        "page_type": "quiz",
        "question_count": 8,
        "interactive_element_count": 42,
        "body_text_preview": "Question 1: What is the powerhouse of the cell?",
    },
    {
        "tabId": 2,
        "title": "Chapter 5 Notes - Google Docs",
        "url": "https://docs.google.com/document/d/abc",
        "active": False,
        "page_type": "article",
        "question_count": 0,
        "interactive_element_count": 10,
        "body_text_preview": "Chapter 5 Biology Notes. The mitochondria is the powerhouse of the cell and generates ATP through oxidative phosphorylation. Cellular respiration occurs in three stages.",
    },
    {
        "tabId": 3,
        "title": "Khan Academy - Biology",
        "url": "https://khanacademy.org/biology",
        "active": False,
        "page_type": "article",
        "question_count": 0,
        "interactive_element_count": 5,
        "body_text_preview": "Learn about the cell structure and function. The endoplasmic reticulum helps in protein synthesis.",
    },
]


# ── Tests ─────────────────────────────────────────────────────────────

def test_build_context():
    ctx = build_multi_tab_context(TABS, "answer the biology quiz questions")
    assert isinstance(ctx, MultiTabContext)
    assert ctx.total_tabs_open == 3
    assert ctx.active_tab.get("tabId") == 1
    assert len(ctx.supporting_tabs) == 2
    assert ctx.context_summary != ""
    print("  ✓ build_multi_tab_context returns valid MultiTabContext")


def test_build_empty():
    ctx = build_multi_tab_context([], "some goal")
    assert ctx.total_tabs_open == 0
    assert ctx.active_tab == {}
    assert ctx.supporting_tabs == []
    assert ctx.cross_tab_facts == []
    print("  ✓ Empty tabs_data returns empty context")


def test_extract_facts():
    tab_texts = [
        ("Notes", "The mitochondria is the powerhouse of the cell. It generates ATP."),
        ("Wiki", "Python is a programming language. Cats are mammals."),
    ]
    facts = extract_relevant_facts(tab_texts, "what is the powerhouse of the cell")
    assert len(facts) >= 1
    assert any("mitochondria" in f.lower() for f in facts)
    print(f"  ✓ extract_relevant_facts found {len(facts)} fact(s)")


def test_extract_no_overlap():
    tab_texts = [
        ("Recipes", "Preheat oven to 350 degrees. Add flour and sugar."),
    ]
    facts = extract_relevant_facts(tab_texts, "solve math equations")
    assert len(facts) == 0
    print("  ✓ No facts when no keyword overlap")


def test_summarize_tabs():
    summary = summarize_tabs(TABS, active_tab_id=1)
    assert "[ACTIVE]" in summary
    assert "[OPEN]" in summary
    assert "Quiz - Chapter 5 Biology" in summary
    lines = summary.strip().split("\n")
    assert len(lines) == 3
    print(f"  ✓ summarize_tabs produces {len(lines)} lines")


def test_summarize_max_tabs():
    many_tabs = [{"tabId": i, "title": f"Tab {i}", "url": f"https://t{i}.com"} for i in range(15)]
    summary = summarize_tabs(many_tabs, max_tabs=10)
    assert "... and 5 more tab(s)" in summary
    print("  ✓ summarize_tabs truncates beyond max_tabs")


def test_format_for_planner():
    ctx = build_multi_tab_context(TABS, "answer the biology quiz questions")
    result = format_for_planner(ctx)
    assert "Open Browser Tabs" in result
    assert "Active Tab Details" in result
    assert len(result) <= 3200
    print(f"  ✓ format_for_planner produces {len(result)} chars")


def test_format_within_limit():
    ctx = build_multi_tab_context(TABS, "answer the biology quiz questions")
    result = format_for_planner(ctx, max_chars=200)
    assert len(result) <= 200
    print("  ✓ format_for_planner respects max_chars")


def test_cross_tab_facts_in_context():
    ctx = build_multi_tab_context(TABS, "what is the powerhouse of the cell")
    # Should find mitochondria fact from the notes tab
    mito_facts = [f for f in ctx.cross_tab_facts if "mitochondria" in f.lower()]
    assert len(mito_facts) >= 1
    print(f"  ✓ Cross-tab facts extracted: {len(ctx.cross_tab_facts)} total, {len(mito_facts)} about mitochondria")


# ── Run ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Testing build_multi_tab_context...")
    test_build_context()
    test_build_empty()

    print("\nTesting extract_relevant_facts...")
    test_extract_facts()
    test_extract_no_overlap()

    print("\nTesting summarize_tabs...")
    test_summarize_tabs()
    test_summarize_max_tabs()

    print("\nTesting format_for_planner...")
    test_format_for_planner()
    test_format_within_limit()

    print("\nTesting cross-tab fact extraction...")
    test_cross_tab_facts_in_context()

    print("\n✓ All Phase 9 tests passed")
