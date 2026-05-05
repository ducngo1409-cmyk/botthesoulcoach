"""User registration and basic command handlers."""

from __future__ import annotations

import logging
from typing import Set

import pytz
from telegram import Update
from telegram.ext import ContextTypes

from config import settings
from db import conn, transaction

log = logging.getLogger(__name__)

# In-memory set of user IDs waiting to reply with their timezone.
# Survives only for the lifetime of the process — good enough for onboarding.
_awaiting_tz: Set[int] = set()

_COMMON_TZ = (
    "Asia/Ho\\_Chi\\_Minh · Asia/Singapore · Asia/Bangkok · "
    "Asia/Tokyo · Asia/Seoul · Europe/London · Europe/Paris · "
    "America/New\\_York · America/Los\\_Angeles · UTC"
)


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

    try:
        pytz.timezone(text)  # raises if invalid
        with transaction() as cx:
            cx.execute("UPDATE users SET tz = ? WHERE tg_id = ?", (text, user.id))
        await update.message.reply_text(
            f"✅ Đã đặt múi giờ: *{text}*. Nhắc nhở của bạn sẽ hiển thị theo giờ địa phương.",
            parse_mode="Markdown",
        )
        log.info("User %s set timezone to %s", user.id, text)
    except Exception:
        await update.message.reply_text(
            f"⚠️ Mình không nhận ra *{text}* là múi giờ hợp lệ — "
            f"giữ nguyên mặc định. Bạn có thể nhắn lại tên múi giờ bất cứ lúc nào.",
            parse_mode="Markdown",
        )
        log.debug("User %s sent invalid tz %r", user.id, text)
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    fresh = _register_user(user.id, user.full_name)

    if fresh:
        await update.message.reply_text(
            f"👋 Xin chào {user.first_name}, mình là Soul Coach của bạn.\n\n"
            "Mình sẽ nhắc nhở bạn về những điều bạn muốn duy trì, "
            "và luôn sẵn sàng lắng nghe khi bạn cần.\n\n"
            "Thử ngay:\n"
            "• /addtask Thiền buổi sáng | 0 8 * * *\n"
            "• /tasks — xem nhắc nhở của bạn\n"
            "• /help — danh sách lệnh đầy đủ\n"
            "• Hoặc cứ nhắn bất cứ điều gì đang trong đầu bạn."
        )
        await update.message.reply_text(
            "🕐 *Bạn đang ở múi giờ nào?*\n\n"
            f"Gợi ý: {_COMMON_TZ}\n\n"
            "_Nhắn tên múi giờ hợp lệ, ví dụ `Asia/Ho_Chi_Minh`. "
            "Bỏ qua nếu muốn giữ mặc định._",
            parse_mode="Markdown",
        )
        _awaiting_tz.add(user.id)
    else:
        await update.message.reply_text("👋 Chào mừng trở lại! Nhắn /help để xem mình có thể làm gì cho bạn.")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    is_supervisor = (
        update.effective_user is not None
        and update.effective_user.id == settings().supervisor_chat_id
    )

    user_cmds = (
        "📋 *Lệnh người dùng*\n"
        "/start — đăng ký\n"
        "/tasks — xem nhắc nhở\n"
        "/addtask <tên> | <cron> — thêm nhắc nhở (cron 5 trường)\n"
        "/removetask <id> — xóa nhắc nhở\n"
        "/pause — tắt nhắc nhở\n"
        "/resume — bật lại nhắc nhở\n"
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
        "/kb\\_promote <interaction\\_id>"
    )
    text = user_cmds + (sup_cmds if is_supervisor else "")
    await update.message.reply_text(text, parse_mode="Markdown")
