"""Smoke test — no Telegram or Gemini calls.

Verifies: imports, DB init, schema, KB seed loaded, KB search, satisfaction
classifier. Run with `python -m tests.test_smoke` from project root.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def main() -> int:
    # Stub env BEFORE importing config
    os.environ.setdefault("TELEGRAM_TOKEN", "test:dummy")
    os.environ.setdefault("SUPERVISOR_CHAT_ID", "1")
    os.environ.setdefault("GEMINI_API_KEY", "dummy")

    # Use a fresh DB in /tmp
    tmp = Path(tempfile.mkdtemp(prefix="soulcoach_smoke_"))
    os.environ["DB_PATH"] = str(tmp / "smoke.db")

    # Make project root importable
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

    print(f"[*] DB at {os.environ['DB_PATH']}")

    # 1. Imports
    print("[*] Importing modules…")
    import db
    from services import kb, satisfaction
    from handlers import admin, escalation, onboarding, qa, tasks  # noqa: F401
    print("    OK")

    # 2. DB init + seed
    print("[*] init_db()…")
    db.init_db()
    print("    OK")

    # 3. Schema sanity
    print("[*] schema check…")
    expected = {
        "users", "tasks", "check_ins", "interactions", "kb_entries",
        "sessions", "escalations", "reports", "audit_log",
    }
    rows = db.conn().execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    have = {r["name"] for r in rows}
    missing = expected - have
    assert not missing, f"missing tables: {missing}"
    print(f"    OK ({len(have)} tables)")

    # 4. KB seeded
    print("[*] KB seeded…")
    entries = kb.list_all()
    assert entries, "KB is empty after init"
    print(f"    OK ({len(entries)} entries)")

    # 5. KB search hit — Vietnamese queries match their categories
    FUZZY_THRESHOLD = 65
    hit_cases = [
        ("không tập trung được hôm nay", "focus"),
        ("mình đang bị quá tải căng thẳng", "stress"),
        ("mất ngủ không ngủ được", "sleep"),
        ("thiếu động lực trì hoãn mãi", "motivation"),
        ("lo lắng hồi hộp bất an", "anxiety"),
    ]
    for query, expected_cat in hit_cases:
        print(f"[*] KB search '{query}'…")
        results = kb.search(query, top_k=3)
        assert results, f"no results for {query!r}"
        top, score = results[0]
        assert top.category == expected_cat, \
            f"expected {expected_cat}, got {top.category} (score={score:.0f})"
        assert score >= FUZZY_THRESHOLD, \
            f"score too low for {query!r}: {score:.0f} < {FUZZY_THRESHOLD}"
        print(f"    OK (cat={top.category}, score={score:.0f})")

    # 6. KB search miss — obscure off-topic queries should score below threshold
    print("[*] KB search obscure questions (should miss)…")
    for obscure in [
        "how do I solder a microcontroller pin",
        "tại sao bầu trời màu xanh",
        "how to refactor a python codebase",
    ]:
        miss = kb.search(obscure, top_k=1)
        score = miss[0][1] if miss else 0
        assert score < FUZZY_THRESHOLD, \
            f"{obscure!r} scored {score:.0f}, should be < {FUZZY_THRESHOLD}"
        print(f"    {obscure!r} -> {score:.0f} (miss ✓)")

    # 7. Satisfaction classifier
    print("[*] satisfaction.classify…")
    cases = [
        ("thanks, that helped a lot", "positive"),
        ("cảm ơn", "positive"),
        ("still stuck honestly", "negative"),
        ("vẫn vậy", "negative"),
        ("how about this idea", "neutral"),
    ]
    for text, expected in cases:
        got = satisfaction.classify(text)
        assert got == expected, f"{text!r}: expected {expected}, got {got}"
        print(f"    {text!r} -> {got}")
    print("    OK")

    # 8. Satisfaction counter flow
    print("[*] satisfaction counter…")
    # Need a user row first because of FK
    with db.transaction() as cx:
        cx.execute("INSERT INTO users (tg_id, name) VALUES (?, ?)", (42, "Test"))
    assert satisfaction.get_counter(42) == 0
    assert satisfaction.increment(42) == 1
    assert satisfaction.increment(42) == 2
    satisfaction.reset(42)
    assert satisfaction.get_counter(42) == 0
    print("    OK")

    # 9. KB CRUD round-trip
    print("[*] KB CRUD…")
    new_id = kb.add("test", "What is X?", "X is a test.", "x, test", created_by=1)
    fetched = kb.get(new_id)
    assert fetched and fetched.question == "What is X?"
    assert kb.edit(new_id, answer="X is updated.")
    assert kb.get(new_id).answer == "X is updated."
    assert kb.delete(new_id)
    assert kb.get(new_id) is None
    print("    OK")

    # 10. Cron validation
    print("[*] cron validation…")
    from handlers.tasks import _validate_cron
    assert _validate_cron("0 8 * * *")
    assert _validate_cron("0 19 * * 1,3,5")
    assert not _validate_cron("not a cron")
    print("    OK")

    print("\n✅ ALL SMOKE TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
