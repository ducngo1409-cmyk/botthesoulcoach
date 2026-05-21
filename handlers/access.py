"""Access control middleware — request-to-join model + onboarding state machine.

Anyone can find the bot and send `/start`. New users land in a `pending`
state and the supervisor gets a DM with **✅ Duyệt** / **❌ Từ chối** inline
buttons. Until approved, the user can only send `/start` (to receive the
same "đang chờ duyệt" message) and `/talk_to_human` (emergency).

Two gates, both running at `group=-1`:

1. **Approval gate** (DB-backed `users.access_status`)
   - `approved` → fall through to gate 2
   - `pending`  → reply once "đang chờ duyệt", drop subsequent updates silently
   - `rejected` → silent drop
   - missing row (user has never /started) → only `/start` proceeds

2. **Onboarding gate** (DB-backed `users.onboarded`)
   - `1` → full access
   - `0` → only `/start`, `/tz <arg>`, `/talk_to_human`, and plain text
     (consumed as a tz reply) proceed. Other commands get a reminder.

Supervisor is always allowed.
"""

from __future__ import annotations

import logging
import time
from typing import Dict

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from config import settings
from db import conn

log = logging.getLogger(__name__)

# Per-user rate-limit for the "still pending" reminder (avoid spamming users
# who keep messaging). 30 s feels right — they realize the bot is muted.
_PENDING_NOTIFY_INTERVAL = 30.0
_pending_last_notify: Dict[int, float] = {}


async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return

    s = settings()

    # Supervisor is always allowed — never blocked by any gate.
    if user.id == s.supervisor_chat_id:
        return

    msg = update.effective_message
    text = (msg.text or "").strip() if msg else ""
    first_token = text.split()[0].lower() if text else ""

    # ============ Gate 1: approval status =============================
    row = conn().execute(
        "SELECT access_status, onboarded FROM users WHERE tg_id = ?", (user.id,)
    ).fetchone()

    if not row:
        # Never registered. Only /start should proceed (it registers + DMs admin).
        if first_token == "/start":
            return
        if msg:
            try:
                await msg.reply_text("Gõ `/start` để bắt đầu.", parse_mode="Markdown")
            except Exception:
                pass
        raise ApplicationHandlerStop

    access = row["access_status"]

    if access == "rejected":
        raise ApplicationHandlerStop  # silent drop

    if access == "pending":
        # /start should reach onboarding.start (which knows to show the "still pending" msg).
        if first_token == "/start":
            return
        # Everything else: rate-limited reminder.
        now = time.time()
        last = _pending_last_notify.get(user.id, 0.0)
        if now - last > _PENDING_NOTIFY_INTERVAL and msg:
            _pending_last_notify[user.id] = now
            try:
                await msg.reply_text(
                    "⏳ Bạn đang trong hàng chờ admin duyệt — mình sẽ nhắn lại "
                    "ngay khi được chấp nhận. Cảm ơn bạn đã kiên nhẫn!"
                )
            except Exception:
                pass
        raise ApplicationHandlerStop

    # ============ Gate 2: mandatory onboarding ========================
    if not s.require_onboarding:
        return
    if row["onboarded"]:
        return

    # ----- User is approved but still onboarding -----------------------
    # Callback queries during onboarding → drop with toast.
    if update.callback_query is not None:
        try:
            await update.callback_query.answer(
                "Hoàn thành thiết lập trước nhé!", show_alert=False
            )
        except Exception:
            pass
        raise ApplicationHandlerStop

    if msg is None:
        raise ApplicationHandlerStop

    if first_token in {"/start", "/talk_to_human", "/tz"}:
        return  # let onboarding & emergency commands through

    # Plain text → consume as tz reply right here.
    if not text.startswith("/"):
        from handlers.onboarding import handle_tz_reply
        await handle_tz_reply(update, context)
        raise ApplicationHandlerStop

    # Any other command: blocked with reminder.
    try:
        await msg.reply_text(
            "🕐 Mình cần biết múi giờ của bạn trước.\n\n"
            "Nhắn tên thành phố (vd `Hanoi`, `Tokyo`, `+7`) "
            "hoặc `skip` để giữ mặc định.\n"
            "Sau khi xong, bạn sẽ dùng được đầy đủ tính năng."
        )
    except Exception:
        pass
    raise ApplicationHandlerStop


# --- Approval callbacks (called from admin handler module) -------------

def approve_user(user_id: int) -> bool:
    with conn():
        cur = conn().execute(
            "UPDATE users SET access_status = 'approved' WHERE tg_id = ? "
            "AND access_status = 'pending'",
            (user_id,),
        )
        return cur.rowcount > 0


def reject_user(user_id: int) -> bool:
    with conn():
        cur = conn().execute(
            "UPDATE users SET access_status = 'rejected' WHERE tg_id = ?",
            (user_id,),
        )
        return cur.rowcount > 0


def list_pending() -> list:
    rows = conn().execute(
        "SELECT tg_id, name, joined_at FROM users "
        "WHERE access_status = 'pending' ORDER BY joined_at"
    ).fetchall()
    return [dict(r) for r in rows]
