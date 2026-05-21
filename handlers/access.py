"""Access control middleware — strict state-machine isolation.

This handler runs at `group=-1` (highest priority) before any feature handler.
It enforces two invariants:

1. **Allowlist** — `ALLOWED_USER_IDS` env var (comma-separated). Empty = open.
   Supervisor is always allowed. Unauthorized users get a "private bot"
   message once, then all their updates are dropped silently.

2. **State isolation for onboarding** — while `users.onboarded = 0`, the only
   actions that proceed are:

       /start                 — restart the flow
       /talk_to_human         — emergency exit to S
       /tz <city|offset>      — alternative way to set tz
       <any plain text>       — interpreted as a tz reply (consumed here)

   Every other command is short-circuited with a friendly "finish tz first"
   reminder. Callback queries (button taps) from un-onboarded users are
   dropped silently.

Persisting onboarding state in DB (column `users.onboarded`) makes this
robust to bot restarts — previously the in-memory `_awaiting_tz` set was
lost on restart and the user got stuck.
"""

from __future__ import annotations

import logging
from typing import Set

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from config import settings
from db import conn

log = logging.getLogger(__name__)

# Commands that bypass onboarding (everything else is blocked until tz is set).
# Note: /help is NOT on this list — see comment below.
_ONBOARD_BYPASS_COMMANDS: Set[str] = {"/start", "/talk_to_human"}

# Notified-once set for allowlist denial. Resets on process restart, which is
# fine — restart is rare and users don't need spam.
_denied_notified: Set[int] = set()


async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pre-dispatch gate. Drops or short-circuits updates that don't belong here."""
    user = update.effective_user
    if user is None:
        return  # channel/anonymous updates pass through

    s = settings()

    # ============ Gate 1: allowlist ===================================
    if s.allowed_user_ids:
        allowed = (user.id in s.allowed_user_ids) or (user.id == s.supervisor_chat_id)
        if not allowed:
            if user.id not in _denied_notified:
                _denied_notified.add(user.id)
                try:
                    msg = update.effective_message
                    if msg:
                        await msg.reply_text(
                            "🔒 Bot này dành riêng cho một nhóm người dùng được mời.\n\n"
                            f"Nếu bạn muốn được thêm vào, vui lòng gửi `user_id` của bạn "
                            f"(`{user.id}`) cho admin để được cấp quyền.",
                            parse_mode="Markdown",
                        )
                except Exception:
                    pass
            log.info("Access denied for user %s (%s)", user.id, user.full_name)
            raise ApplicationHandlerStop

    # ============ Gate 2: mandatory onboarding ========================
    if not s.require_onboarding:
        return

    row = conn().execute(
        "SELECT onboarded FROM users WHERE tg_id = ?", (user.id,)
    ).fetchone()
    if not row:
        # Not yet registered — only /start should be reaching here, and it
        # creates the row. Any other input means the user is messaging
        # without /start; let it fall through (the qa or command handler
        # decides what to do — typically nothing useful, but not our job).
        return
    if row["onboarded"]:
        return  # onboarded — full access

    # ----- User is onboarding. Strict isolation. ----------------------
    msg = update.effective_message

    # Callback queries (mood emoji, KB approve, etc.) — drop silently during
    # onboarding. There shouldn't be any pending callbacks anyway.
    if update.callback_query is not None:
        try:
            await update.callback_query.answer("Hoàn thành thiết lập trước nhé!", show_alert=False)
        except Exception:
            pass
        raise ApplicationHandlerStop

    if msg is None:
        raise ApplicationHandlerStop

    text = (msg.text or "").strip()
    first_token = text.split()[0].lower() if text else ""

    # /start always works — restarts the flow.
    if first_token == "/start":
        return

    # /talk_to_human works as an emergency exit.
    if first_token == "/talk_to_human":
        return

    # /tz <arg> works as an alternative way to set tz (it marks onboarded=1).
    if first_token == "/tz":
        return

    # Plain text → route to handle_tz_reply RIGHT HERE (don't let it reach
    # qa/KB/LLM). This way state machine is truly isolated: in onboarding
    # state, every text reply is a tz attempt — never something else.
    if not text.startswith("/"):
        from handlers.onboarding import handle_tz_reply
        await handle_tz_reply(update, context)
        raise ApplicationHandlerStop

    # Any other command (/tasks, /addtask, /help, /pause, etc.): blocked
    # with a clear reminder pointing at the tz prompt.
    try:
        await msg.reply_text(
            "🕐 Mình cần biết múi giờ của bạn trước.\n\n"
            "Nhắn tên thành phố (vd `Hanoi`, `Tokyo`, `+7`) "
            "hoặc `skip` để giữ mặc định.\n"
            "Sau khi xong, bạn sẽ dùng được đầy đủ tính năng.",
        )
    except Exception:
        pass
    raise ApplicationHandlerStop
