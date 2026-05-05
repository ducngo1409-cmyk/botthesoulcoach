"""One-time migration: add Vietnamese keywords to existing KB entries.

Run ONCE on the server after deploying v2.2:

    cd ~/Bot_The_Soul_Coach
    source .venv/bin/activate
    python deploy/migrate_vi_keywords.py

Safe to run multiple times — only appends VI keywords if they are not already
present in the keywords field.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow running from project root or deploy/
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

os.environ.setdefault("TELEGRAM_TOKEN", "migration-dummy")
os.environ.setdefault("SUPERVISOR_CHAT_ID", "1")
os.environ.setdefault("GEMINI_API_KEY", "migration-dummy")

import db
db.init_db()

# Map: (category, English question fragment) → additional VI keywords to append
VI_ADDITIONS = {
    "focus": (
        "không tập trung, mất tập trung, phân tâm, tập trung, khó tập trung, "
        "mãi không làm được, đầu óc lơ đãng, làm việc hiệu quả"
    ),
    "stress": (
        "căng thẳng, stress, quá tải, lo lắng, áp lực, bị áp đảo, "
        "quá nhiều việc, không chịu nổi, kiệt sức, mệt mỏi tinh thần"
    ),
    "sleep": (
        "mất ngủ, không ngủ được, khó ngủ, thức đêm, trằn trọc, "
        "ngủ không sâu, hay thức giấc, mệt mà không ngủ được"
    ),
    "motivation": (
        "thiếu động lực, lười biếng, trì hoãn, không muốn làm, không có năng lượng, "
        "không thấy hứng, mãi không bắt đầu được, chây lười, không có cảm hứng"
    ),
    "anxiety": (
        "lo âu, lo lắng, hồi hộp, sợ hãi, lo ngại, lo sợ, "
        "cứ nghĩ mãi, không dứt lo được, bất an, căng thẳng lo lắng"
    ),
    "habits": (
        "thói quen, xây dựng thói quen, duy trì thói quen, kiên trì, đều đặn, "
        "thói quen hàng ngày, lịch trình, tạo thói quen mới"
    ),
    "relationships": (
        "cãi nhau, mâu thuẫn, xung đột, tranh cãi, tình cảm, bạn bè, gia đình, "
        "người thân, mâu thuẫn tình cảm, hiểu lầm, giận nhau"
    ),
    "general": (
        "muốn chia sẻ, lắng nghe, tâm sự, cô đơn, buồn, "
        "cần nói chuyện, không biết chia sẻ với ai, nói chuyện"
    ),
}

rows = db.conn().execute(
    "SELECT id, category, keywords FROM kb_entries"
).fetchall()

updated = 0
for row in rows:
    cat = row["category"]
    vi = VI_ADDITIONS.get(cat, "")
    if not vi:
        continue
    existing = row["keywords"] or ""
    # Only append if the VI keywords aren't already there
    if "không tập trung" in existing or "mất ngủ" in existing or \
       "lo âu" in existing or "thói quen" in existing or \
       "cãi nhau" in existing or "muốn chia sẻ" in existing or \
       "thiếu động lực" in existing or "căng thẳng" in existing:
        print(f"  id={row['id']} ({cat}) — VI keywords already present, skipping")
        continue
    new_kw = f"{existing}, {vi}" if existing.strip() else vi
    with db.transaction() as cx:
        cx.execute(
            "UPDATE kb_entries SET keywords = ? WHERE id = ?",
            (new_kw, row["id"]),
        )
    print(f"  ✅ id={row['id']} ({cat}) — VI keywords added")
    updated += 1

# Invalidate KB cache so bot picks up changes immediately
from services.kb import invalidate_cache
invalidate_cache()

print(f"\nDone. {updated} entries updated.")
if updated == 0:
    print("(All entries already had VI keywords — nothing to do.)")
