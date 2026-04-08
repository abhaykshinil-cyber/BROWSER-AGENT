"""Quick integration test for Phase 4 memory system."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from memory.db import MemoryDB
from memory.episodic_store import EpisodicStore
from memory.semantic_store import SemanticStore
from memory.site_profiles import SiteProfileStore
from memory.retrieval import MemoryRetriever
from memory.embeddings import compute_similarity
from schemas import MemoryItem, MemoryType

DB_PATH = "./database/_test_phase4.db"


def main():
    # Clean up any leftover test DB
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    # 1. Init DB
    db = MemoryDB(DB_PATH)
    db.init()
    tables = db.run_query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    table_names = [t["name"] for t in tables]
    print("Tables:", table_names)
    assert "task_runs" in table_names
    assert "memory_rules" in table_names
    assert "site_profiles" in table_names

    # 2. Episodic store
    ep = EpisodicStore(db)
    ep.save_run("t1", "Search GitHub for BrowserAgent", [{"action": "click"}], [{"success": True}], True, "github.com")
    ep.save_run("t2", "Login to Gmail", [{"action": "type"}], [{"success": False}], False, "mail.google.com")
    runs = ep.get_runs(limit=10)
    print(f"Episodic: {len(runs)} runs saved")
    assert len(runs) == 2

    similar = ep.get_similar_runs("search github repos", limit=3)
    print(f"Similar runs for 'search github repos': {len(similar)}")

    stats = ep.get_success_rate()
    print(f"Success rate: {stats}")

    # 3. Semantic store
    sem = SemanticStore(db)
    rule = MemoryItem(
        type=MemoryType.USER_RULE,
        scope="github.com",
        domain="github.com",
        instruction="Use Ctrl+K to search on GitHub instead of clicking",
        trigger_conditions=["on github.com", "wants to search"],
        preferred_actions=["keyboard shortcut"],
        avoid_actions=["click search bar"],
    )
    mid = sem.save_rule(rule)
    print(f"Saved rule: {mid}")

    rules = sem.get_rules(type_filter="user_rule")
    print(f"User rules: {len(rules)}")
    assert len(rules) == 1

    relevant = sem.get_relevant_rules("search for repos", domain="github.com")
    print(f"Relevant rules for 'search for repos': {len(relevant)}")

    sem.record_outcome(mid, True)
    sem.record_outcome(mid, True)
    sem.record_outcome(mid, False)
    updated = sem.get_rules(type_filter="user_rule")[0]
    print(f"After 2 success + 1 failure: confidence={updated.confidence:.3f}")

    # 4. Site profiles
    sites = SiteProfileStore(db)
    sites.save_profile("github.com", {
        "next_button_patterns": ["Next", "Continue"],
        "submit_button_patterns": ["Submit", "Create"],
        "mcq_selectors": [],
        "custom_notes": "Uses Ctrl+K for search"
    })
    profile = sites.get_profile("github.com")
    print(f"Site profile: {profile['domain']}, patterns: {len(profile['next_button_patterns'])}")

    sites.update_profile("github.com", {"next_button_patterns": ["Proceed"], "custom_notes": "Has dark mode"})
    profile2 = sites.get_profile("github.com")
    print(f"Updated patterns: {profile2['next_button_patterns']}")
    print(f"Merged notes: {profile2['custom_notes']}")

    # 5. Retrieval engine
    retriever = MemoryRetriever(db)
    context = retriever.get_context_for_task(
        goal="Search for BrowserAgent repos",
        domain="github.com",
        task_type="search"
    )
    print(f"Retrieved {len(context)} memories for planning")
    for item in context:
        print(f"  [{item.type.value}] {item.instruction[:60]}")

    # 6. Embeddings
    s1 = compute_similarity("click the submit button", "press submit")
    s2 = compute_similarity("navigate to github", "buy groceries")
    print(f"Similarity (similar): {s1:.3f}")
    print(f"Similarity (different): {s2:.3f}")
    assert s1 > s2

    # Cleanup
    db.close()
    os.remove(DB_PATH)

    print("\n✓ All Phase 4 tests passed")


if __name__ == "__main__":
    main()
