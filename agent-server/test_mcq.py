"""Quick test for the MCQ solver parsing logic (no API key needed)."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from core.mcq_solver import (
    parse_user_instruction,
    match_memory_rules,
    _parse_response,
    Answer,
)

def test_parse_user_instruction():
    questions = [
        {"qIdx": 0, "text": "What is 2+2?", "options": [{"idx": 0, "text": "3"}, {"idx": 1, "text": "4"}, {"idx": 2, "text": "5"}], "answered": False},
        {"qIdx": 1, "text": "Capital of France?", "options": [{"idx": 0, "text": "London"}, {"idx": 1, "text": "Paris"}], "answered": False},
    ]

    # Test 1: "select B" → index 1 for all questions
    overrides = parse_user_instruction("select B", questions)
    assert len(overrides) == 2, f"Expected 2 overrides, got {len(overrides)}"
    assert overrides[0].selected_indices == [1], f"Q0: expected [1], got {overrides[0].selected_indices}"
    assert overrides[1].selected_indices == [1], f"Q1: expected [1], got {overrides[1].selected_indices}"
    assert overrides[0].source == "user_instruction"
    print("  ✓ 'select B' → index 1 for all questions")

    # Test 2: "for question 1 select C"
    overrides2 = parse_user_instruction("for question 1, select C", questions)
    assert 0 in overrides2, f"Expected q0 override, got keys: {list(overrides2.keys())}"
    assert overrides2[0].selected_indices == [2]
    print("  ✓ 'for question 1 select C' → q0 index 2")

    # Test 3: "the answer is Paris"
    overrides3 = parse_user_instruction("the answer is Paris", questions)
    assert 1 in overrides3, f"Expected q1 override"
    assert overrides3[1].selected_indices == [1]
    print("  ✓ 'the answer is Paris' → q1 index 1")

    # Test 4: "select A, C"
    overrides4 = parse_user_instruction("select A and C", questions)
    assert len(overrides4) == 2
    assert overrides4[0].selected_indices == [0, 2]
    print("  ✓ 'select A and C' → indices [0, 2]")


def test_parse_response():
    # Test clean JSON
    r1 = _parse_response('{"answers": [{"qIdx": 0, "selected": [1], "reasoning": "test", "confidence": 0.9, "source": "ai_knowledge"}]}')
    assert len(r1) == 1 and r1[0]["qIdx"] == 0 and r1[0]["selected"] == [1]
    print("  ✓ Clean JSON parsed")

    # Test markdown-wrapped
    r2 = _parse_response('```json\n{"answers": [{"qIdx": 0, "selected": [2], "reasoning": "x", "confidence": 1.0, "source": "ai_knowledge"}]}\n```')
    assert len(r2) == 1 and r2[0]["selected"] == [2]
    print("  ✓ Markdown-wrapped JSON parsed")

    # Test with preamble text
    r3 = _parse_response('Here is my answer:\n{"answers": [{"qIdx": 0, "selected": [0], "reasoning": "y", "confidence": 0.5, "source": "ai_knowledge"}]}')
    assert len(r3) == 1
    print("  ✓ JSON with preamble parsed")


def test_memory_rules():
    questions = [
        {"qIdx": 0, "text": "Which Python version supports pattern matching?", "options": [{"idx": 0, "text": "3.8"}, {"idx": 1, "text": "3.10"}, {"idx": 2, "text": "3.6"}], "answered": False},
    ]
    memories = [
        {
            "instruction": "Python pattern matching was introduced in version 3.10",
            "trigger_conditions": ["python", "pattern matching"],
            "preferred_actions": ["select 3.10"],
        }
    ]

    overrides = match_memory_rules(questions, memories)
    assert 0 in overrides
    assert overrides[0].selected_indices == [1]  # 3.10 is index 1
    assert overrides[0].source == "user_rule"
    print("  ✓ Memory rule matched: Python 3.10")


if __name__ == "__main__":
    print("Testing user instruction parsing...")
    test_parse_user_instruction()
    print("\nTesting response parsing...")
    test_parse_response()
    print("\nTesting memory rule matching...")
    test_memory_rules()
    print("\n✓ All MCQ solver tests passed")
