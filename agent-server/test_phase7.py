"""Quick tests for Phase 7 — Policy Engine + Rule Merger (no API key needed)."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime, timezone
from schemas import MemoryItem, MemoryType
from core.policy_engine import (
    validate_memory_item,
    apply_rules_to_plan,
    _fallback_parse,
)
from memory.rule_merger import (
    compute_text_similarity,
    find_similar_rules,
    are_conflicting,
    merge_rules,
    suggest_consolidation,
)

def _make_rule(**kwargs):
    """Helper to create a MemoryItem with defaults."""
    now = datetime.now(timezone.utc)
    defaults = dict(
        memory_id="test_" + str(id(kwargs))[-6:],
        type=MemoryType.USER_RULE,
        scope="global",
        domain=None,
        instruction="Test instruction",
        trigger_conditions=["test"],
        preferred_actions=["do something"],
        avoid_actions=[],
        confidence=1.0,
        success_count=0,
        failure_count=0,
        created_at=now,
        updated_at=now,
    )
    defaults.update(kwargs)
    return MemoryItem(**defaults)


# ── validate_memory_item ──────────────────────────────────────────

def test_validate_valid():
    item = _make_rule(instruction="Always select option A on quizzes")
    result = validate_memory_item(item)
    assert result["valid"] is True
    print("  ✓ Valid item passes validation")

def test_validate_empty_instruction():
    item = _make_rule(instruction="")
    result = validate_memory_item(item)
    assert result["valid"] is False
    assert any("empty" in w.lower() for w in result["warnings"])
    print("  ✓ Empty instruction fails validation")

def test_validate_no_triggers():
    item = _make_rule(trigger_conditions=[])
    result = validate_memory_item(item)
    assert result["valid"] is True  # soft warning
    assert any("trigger" in w.lower() for w in result["warnings"])
    print("  ✓ No triggers produces warning")

def test_validate_site_no_domain():
    item = _make_rule(type=MemoryType.SITE, domain=None)
    result = validate_memory_item(item)
    assert any("domain" in w.lower() for w in result["warnings"])
    print("  ✓ Site type without domain produces warning")

def test_validate_short_instruction():
    item = _make_rule(instruction="short")
    result = validate_memory_item(item)
    assert any("short" in w.lower() for w in result["warnings"])
    print("  ✓ Short instruction produces warning")


# ── apply_rules_to_plan ──────────────────────────────────────────

def test_apply_rules_removes_avoided():
    steps = [
        {"action_type": "CLICK", "reason": "click the notification bell"},
        {"action_type": "TYPE",  "reason": "type search query"},
    ]
    rules = [
        _make_rule(avoid_actions=["click the notification bell"]),
    ]
    result = apply_rules_to_plan(steps, rules)
    assert len(result) == 1
    assert result[0]["action_type"] == "TYPE"
    print("  ✓ Avoided step removed from plan")

def test_apply_rules_annotates_preferred():
    steps = [
        {"action_type": "CLICK", "reason": "use keyboard shortcut"},
    ]
    rules = [
        _make_rule(preferred_actions=["use keyboard shortcut"]),
    ]
    result = apply_rules_to_plan(steps, rules)
    assert len(result) == 1
    assert "rule_applied" in result[0]
    print("  ✓ Preferred step annotated with rule_applied")


# ── fallback_parse ────────────────────────────────────────────────

def test_fallback_parse_domain():
    item = _fallback_parse("On github.com, always use Ctrl+K", None, None)
    assert item.domain == "github.com"
    assert item.type == MemoryType.SITE
    print("  ✓ Fallback parse detects domain from text")

def test_fallback_parse_global():
    item = _fallback_parse("Always select the first option", None, None)
    assert item.domain is None
    assert item.scope == "global"
    print("  ✓ Fallback parse creates global rule")


# ── compute_text_similarity ──────────────────────────────────────

def test_similarity_identical():
    sim = compute_text_similarity("select option A always", "select option A always")
    assert sim == 1.0
    print("  ✓ Identical texts have similarity 1.0")

def test_similarity_different():
    sim = compute_text_similarity("click the red button", "open the blue link")
    assert sim < 0.3
    print(f"  ✓ Different texts have low similarity ({sim:.3f})")

def test_similarity_partial():
    sim = compute_text_similarity(
        "always select first option on quiz",
        "select the first option for every quiz question"
    )
    assert sim > 0.4
    print(f"  ✓ Partial overlap has moderate similarity ({sim:.3f})")


# ── find_similar_rules ───────────────────────────────────────────

def test_find_similar():
    new_rule = _make_rule(
        memory_id="new1",
        instruction="Always select the first option on quizzes"
    )
    existing = [
        _make_rule(memory_id="e1", instruction="Select the first option for every quiz question"),
        _make_rule(memory_id="e2", instruction="Wait 3 seconds after clicking Next"),
    ]
    similar = find_similar_rules(new_rule, existing, threshold=0.3)
    assert len(similar) >= 1
    assert similar[0].memory_id == "e1"
    print(f"  ✓ Found {len(similar)} similar rule(s)")


# ── are_conflicting ──────────────────────────────────────────────

def test_conflicting():
    a = _make_rule(
        memory_id="a",
        preferred_actions=["click the notification bell"],
        avoid_actions=[],
    )
    b = _make_rule(
        memory_id="b",
        preferred_actions=[],
        avoid_actions=["click the notification bell"],
    )
    assert are_conflicting(a, b) is True
    print("  ✓ Conflicting rules detected")

def test_not_conflicting():
    a = _make_rule(
        memory_id="a",
        instruction="Wait after navigation",
        preferred_actions=["wait 3 seconds"],
        avoid_actions=[],
    )
    b = _make_rule(
        memory_id="b",
        instruction="Select the first MCQ option always",
        preferred_actions=["select option A"],
        avoid_actions=[],
    )
    assert are_conflicting(a, b) is False
    print("  ✓ Non-conflicting rules correctly identified")


# ── merge_rules ──────────────────────────────────────────────────

def test_merge():
    a = _make_rule(
        memory_id="base1",
        instruction="Select the first option",
        trigger_conditions=["taking a quiz"],
        preferred_actions=["select option A"],
        confidence=0.8,
    )
    b = _make_rule(
        memory_id="new1",
        instruction="Always select the first option on any MCQ quiz question",
        trigger_conditions=["taking a quiz", "question type is radio"],
        preferred_actions=["select option A", "do not use AI knowledge"],
        confidence=1.0,
    )
    merged = merge_rules(a, b)
    assert merged.memory_id == "base1"  # keeps base ID
    assert "MCQ" in merged.instruction  # longer instruction wins
    assert len(merged.trigger_conditions) >= 2
    assert merged.confidence == 0.9  # average
    print("  ✓ Merge combines triggers, prefers longer instruction, averages confidence")


# ── suggest_consolidation ────────────────────────────────────────

def test_consolidation():
    rules = [
        _make_rule(
            memory_id="c1",
            instruction="select first option quiz question",
            trigger_conditions=["taking quiz"],
        ),
        _make_rule(
            memory_id="c2",
            instruction="select first option quiz question always",
            trigger_conditions=["taking quiz", "radio question"],
        ),
        _make_rule(memory_id="c3", instruction="Wait 3 seconds after clicking Next"),
    ]
    pairs = suggest_consolidation(rules)
    assert len(pairs) >= 1, f"Expected at least 1 pair, got {len(pairs)}"
    assert pairs[0][2] > 0.5  # similarity > 0.5
    print(f"  ✓ Found {len(pairs)} consolidation pair(s) (sim={pairs[0][2]:.3f})")


# ── Main ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Testing validate_memory_item...")
    test_validate_valid()
    test_validate_empty_instruction()
    test_validate_no_triggers()
    test_validate_site_no_domain()
    test_validate_short_instruction()

    print("\nTesting apply_rules_to_plan...")
    test_apply_rules_removes_avoided()
    test_apply_rules_annotates_preferred()

    print("\nTesting fallback_parse...")
    test_fallback_parse_domain()
    test_fallback_parse_global()

    print("\nTesting compute_text_similarity...")
    test_similarity_identical()
    test_similarity_different()
    test_similarity_partial()

    print("\nTesting find_similar_rules...")
    test_find_similar()

    print("\nTesting are_conflicting...")
    test_conflicting()
    test_not_conflicting()

    print("\nTesting merge_rules...")
    test_merge()

    print("\nTesting suggest_consolidation...")
    test_consolidation()

    print("\n✓ All Phase 7 tests passed")
