"""User registration and basic command handlers."""

from __future__ import annotations

import logging
from typing import Set

from telegram import Update
from telegram.ext import ContextTypes

from config import settings
from db import conn, transaction
from services.tz_aliases import resolve_tz

log = logging.getLogger(__name__)

# In-memory set of user IDs waiting to reply with their timezone.
# Survives only for the lifetime of the process — good enough for onboarding.
_awaiting_tz: Set[int] = set()


def is_awaiting_tz(user_id: int) -> bool:
    return user_id in _awaiting_tz


def _register_user(tg_id: int, name: str | None) -> bool:
    """Insert user if new. Returns True iff inserted."""
    s = settings()
    with transaction() as cx:
        cur = cx.execute(
            "INSERT OR IGNORE INTO users (tg_id, name, tz) VALUES (?, ?, ?)",
            (tg_id, name or "", s.default_tz),
        )
        return cur.rowcount > 0


async def handle_tz_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Consume the next message from a new user as a timezone reply.

    Returns True if this message was consumed (caller should return immediately).
    """
    user = update.effective_user
    if user is None or user.id not in _awaiting_tz:
        return False

    _awaiting_tz.discard(user.id)
    text = (update.message.text or "").strip()

    # Explicit skip only — "không" alone is too risky (it's a very common VI word).
    # User must type the exact keyword to skip onboarding.
    if text.lower().strip() in {"skip", "bỏ qua", "bo qua", "/skip"}:
        await update.message.reply_text(
            "👌 Giữ múi giờ mặc định *Asia/Ho_Chi_Minh*. "
            "Đổi bất cứ lúc nào bằng `/tz <thành phố>`.",
            parse_mode="Markdown",
        )
        return True

    iana = resolve_tz(text)
    if iana:
        with transaction() as cx:
            cx.execute("UPDATE users SET tz = ? WHERE tg_id = ?", (iana, user.id))
        await update.message.reply_text(
            f"✅ Đã đặt múi giờ *{iana}*. Nhắc nhở sẽ hiển thị theo giờ địa phương của bạn.",
            parse_mode="Markdown",
        )
        log.info("User %s set tz %r → %s", user.id, text, iana)
    else:
        # Keep the user in the awaiting state so they can try again without /start.
        _awaiting_tz.add(user.id)
        await update.message.reply_text(
            f"⚠️ Mình chưa nhận ra *{text}*. Thử lại với:\n"
            "• Tên thành phố: `Hanoi`, `Tokyo`, `Singapore`, `London`\n"
            "• Quốc gia: `Vietnam`, `Japan`, `UK`\n"
            "• Múi giờ: `+7`, `UTC+9`, `GMT-5`\n"
            "• Hoặc nhắn `skip` để giữ mặc định",
            parse_mode="Markdown",
        )
        log.debug("User %s sent unrecognized tz %r", user.id, text)
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    fresh = _register_user(user.id, user.full_name)

    if fresh:
        await update.message.reply_text(
            f"👋 Xin chào {user.first_name}, mình là *Soul Coach* của bạn.\n\n"
            "Mình giúp bạn duy trì những thói quen tốt và lắng nghe khi cần.\n\n"
            "Thử ngay:\n"
            "• `/addtask Thiền | daily 7:00`\n"
            "• `/tasks` — xem nhắc nhở của bạn\n"
            "• `/help` — danh sách lệnh đầy đủ\n"
            "• Hoặc cứ nhắn bất cứ điều gì đang trong đầu bạn.",
            parse_mode="Markdown",
        )
        await update.message.reply_text(
            "🕐 *Bạn đang ở thành phố nào?*\n"
            "_Ví dụ: `Hanoi`, `Saigon`, `Tokyo`, `+7`, hoặc `skip` để bỏ qua._",
            parse_mode="Markdown",
        )
        _awaiting_tz.add(user.id)
    else:
        await update.message.reply_text(
            f"👋 Chào mừng trở lại, {user.first_name}! Nhắn /help để xem mình có thể làm gì."
        )


async def tz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/tz <city|country|offset> — set or change timezone any time."""
    user = update.effective_user
    if user is None:
        return
    if not context.args:
        row = conn().execute("SELECT tz FROM users WHERE tg_id = ?", (user.id,)).fetchone()
        current = row["tz"] if row else "?"
        await update.message.reply_text(
            f"🕐 Múi giờ hiện tại: *{current}*\n"
            "Đổi bằng `/tz <thành phố hoặc múi giờ>`\n"
            "Ví dụ: `/tz Hanoi`, `/tz Tokyo`, `/tz +7`",
            parse_mode="Markdown",
        )
        return
    arg = " ".join(context.args).strip()
    iana = resolve_tz(arg)
    if iana:
        with transaction() as cx:
            cx.execute("UPDATE users SET tz = ? WHERE tg_id = ?", (iana, user.id))
        await update.message.reply_text(
            f"✅ Đã đặt múi giờ *{iana}*.", parse_mode="Markdown"
        )
        log.info("User %s changed tz to %s via /tz", user.id, iana)
    else:
        await update.message.reply_text(
            f"⚠️ Không nhận ra *{arg}*. Thử `Hanoi`, `Tokyo`, hay `+7`.",
            parse_mode="Markdown",
        )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    is_supervisor = (
        update.effective_user is not None
        and update.effective_user.id == settings().supervisor_chat_id
    )

    user_cmds = (
        "📋 *Lệnh người dùng*\n"
        "/start — đăng ký\n"
        "/tz \\[thành phố] — xem/đổi múi giờ (vd `/tz Tokyo`)\n"
        "/tasks — xem nhắc nhở\n"
        "/addtask <tên> | <giờ> — thêm nhắc nhở\n"
        "  vd: `/addtask Thiền | daily 7:00`\n"
        "  vd: `/addtask Họp | every monday 9:00`\n"
        "  vd: `/addtask Báo cáo | weekdays 17:30`\n"
        "/removetask <id> — xóa nhắc nhở\n"
        "/pause \\[id] — tắt 1 nhắc (hoặc tất cả nếu không có id)\n"
        "/resume \\[id] — bật lại\n"
        "/nudge <id> <hours> — set giờ nhắc lại (0 = tắt nhắc lại)\n"
        "/talk\\_to\\_human — kết nối với coach con người\n\n"
        "_Hoặc cứ nhắn bất cứ điều gì, mình sẽ cố giúp._"
    )
    sup_cmds = (
        "\n\n👤 *Lệnh supervisor*\n"
        "/users — danh sách người dùng\n"
        "/report — gửi báo cáo tuần ngay\n"
        "/resolve <user\\_id> — đóng escalation\n"
        "/transcript <user\\_id> \\[YYYY-WW] — xem lịch sử hội thoại\n"
        "/settask <user\\_id> | <tên> | <cron> — giao nhắc nhở cho user\n"
        "/debug — xem trạng thái bot, escalations, lỗi gần nhất\n"
        "/kb\\_add <cat> | <q> | <a> | <kw>\n"
        "/kb\\_list \\[cat]\n"
        "/kb\\_edit <id> <field>=<value>\n"
        "/kb\\_del <id>\n"
        "/kb\\_pending — xem entries chờ duyệt\n"
        "/kb\\_approve <id> \\[cat] \\[kw] — duyệt entry\n"
        "/kb\\_reject <id> — từ chối entry\n"
        "/kb\\_promote <interaction\\_id>"
    )
    text = user_cmds + (sup_cmds if is_supervisor else "")
    await update.message.reply_text(text, parse_mode="Markdown")
