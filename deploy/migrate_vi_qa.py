"""One-time migration: update KB Q&A to Vietnamese.

Run ONCE after deploying v2.4:

    cd ~/Bot_The_Soul_Coach
    source .venv/bin/activate
    python deploy/migrate_vi_qa.py

Safe to re-run — checks if questions are already Vietnamese before updating.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

os.environ.setdefault("TELEGRAM_TOKEN", "migration-dummy")
os.environ.setdefault("SUPERVISOR_CHAT_ID", "1")
os.environ.setdefault("GEMINI_API_KEY", "migration-dummy")

import db
db.init_db()

# Map category → (question_vi, answer_vi)
VI_QA = {
    "focus": (
        "Hôm nay mình không tập trung được, có cách nào giúp không?",
        "Thử kỹ thuật Pomodoro 25/5: làm 1 việc 25 phút, nghỉ 5 phút, lặp 4 lần rồi nghỉ dài 30 phút.\n"
        "Đóng hết tab không cần thiết. Nếu suy nghĩ cứ lởn vởn, viết ra giấy —\n"
        "não sẽ thôi bám vì biết nó đã được \"lưu\" rồi.",
    ),
    "stress": (
        "Mình đang cảm thấy bị quá tải",
        "Dừng lại 60 giây. Hít vào 4s, giữ 4s, thở ra 6s — lặp 5 lần.\n"
        "Cách này kích hoạt hệ thần kinh phó giao cảm, giảm cortisol.\n"
        "Sau đó viết ra 3 điều đang nặng đầu nhất và chọn MỘT cái để làm tiếp theo.\n"
        "Quá tải thường đến từ việc cố ôm hết mọi thứ cùng lúc.",
    ),
    "sleep": (
        "Mình mất ngủ, không ngủ được ban đêm",
        "Ba điều tối nay: (1) tắt màn hình 30 phút trước khi ngủ,\n"
        "(2) phòng mát ~19°C, (3) nếu nằm 20 phút chưa ngủ được thì dậy,\n"
        "ngồi chỗ tối, đọc sách giấy buồn tẻ rồi thử lại.\n"
        "Đừng nằm cố vì như vậy não sẽ quen liên kết giường với lo lắng.",
    ),
    "motivation": (
        "Mình không có động lực để bắt đầu",
        "Động lực đến sau hành động, không phải trước.\n"
        "Cam kết chỉ 2 phút thôi — mở file, mang giày, viết một câu.\n"
        "Rào cản thật sự là chi phí bắt đầu; một khi đã bắt đầu, đà sẽ tự cuốn đi.\n"
        "Nếu 2 phút vẫn còn ngại, thì 30 giây thôi.",
    ),
    "anxiety": (
        "Mình cứ lo lắng mãi về một điều gì đó",
        "Thử kỹ thuật 5-4-3-2-1: nêu tên 5 thứ nhìn thấy, 4 thứ chạm được,\n"
        "3 thứ nghe được, 2 thứ ngửi được, 1 thứ nếm được.\n"
        "Cách này kéo não ra khỏi vòng lo lắng và về hiện tại.\n"
        "Nếu lo về vấn đề thật, đặt \"khung lo\" 15 phút sau và tự nhủ sẽ suy nghĩ đúng lúc đó.",
    ),
    "habits": (
        "Làm sao để xây dựng một thói quen mới?",
        "Ghép thói quen mới vào sau thói quen cũ: \"sau khi đánh răng, mình sẽ thiền 1 phút\".\n"
        "Tuần đầu giữ thật nhỏ — mục tiêu không phải kết quả, mà là việc xuất hiện.\n"
        "Đánh dấu X vào lịch giấy mỗi ngày. Đừng để đứt chuỗi.",
    ),
    "relationships": (
        "Mình vừa cãi nhau với người thân",
        "Cho qua vài tiếng trước khi phản ứng — cảm xúc bùng phát rồi tự xẹp nhanh hơn mình nghĩ.\n"
        "Khi nói chuyện lại, mở đầu bằng phía của họ dù mình không đồng ý: \"Mình hiểu bạn cảm thấy X\".\n"
        "Nói như vậy giúp hạ nhiệt. Sau đó mới chia sẻ cảm xúc của mình — dạng cảm xúc, không phải buộc tội.",
    ),
    "general": (
        "Mình chỉ muốn tâm sự thôi",
        "Mình đây. Kể mình nghe đi — không cần bắt đầu theo cách nào cụ thể.\n"
        "Nếu không biết bắt đầu từ đâu, thử \"Hôm nay mình cảm thấy...\" và cứ để nó chảy tự nhiên.",
    ),
}

rows = db.conn().execute(
    "SELECT id, category, question FROM kb_entries WHERE created_by IS NULL"
).fetchall()

updated = 0
for row in rows:
    cat = row["category"]
    vi = VI_QA.get(cat)
    if not vi:
        continue
    q_vi, a_vi = vi
    # Skip if already Vietnamese (contains Vietnamese characters)
    if any(ord(c) > 127 for c in row["question"]):
        print(f"  id={row['id']} ({cat}) — already Vietnamese, skipping")
        continue
    with db.transaction() as cx:
        cx.execute(
            "UPDATE kb_entries SET question = ?, answer = ? WHERE id = ?",
            (q_vi, a_vi, row["id"]),
        )
    print(f"  ✅ id={row['id']} ({cat}) — updated to Vietnamese")
    updated += 1

from services.kb import invalidate_cache
invalidate_cache()

print(f"\nDone. {updated} entries updated.")
if updated == 0:
    print("(All seed entries already Vietnamese — nothing to do.)")
