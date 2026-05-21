"""Access control middleware.

Two gates applied to every update before dispatching to feature handlers:

1. **Allowlist** — if `ALLOWED_USER_IDS` is non-empty, only those IDs (plus the
   supervisor) may interact. Unknown users get a polite "private bot" message
   and the update is dropped.

2. **Mandatory onboarding** — if `REQUIRE_ONBOARDING=1`, a registered user who
   has not yet set their timezone is asked to finish onboarding before any
   command other than the whitelisted set (`/start`, `/tz`, `/help`,
   `/talk_to_human`, `skip` keyword) is processed.

Both gates are TypeHandlers installed at group=-1 (highest priority) so they
run before any feature handler.
"""

from __future__ import annotations

import logging
from typing import Set

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from config import settings
from db import conn

log = logging.getLogger(__name__)

# Commands a not-yet-onboarded user is still allowed to send.
_ONBOARD_BYPASS_COMMANDS: Set[str] = {
    "/start", "/help", "/tz", "/talk_to_human",
}

# A short message shown once when access is denied. Rate-limited per user.
_denied_notified: Set[int] = set()


async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pre-dispatch gate. Drops or short-circuits unauthorized / pre-onboarded updates."""
    user = update.effective_user
    if user is None:
        return  # callback queries without user, channel updates, etc.

    s = settings()

    # --- Gate 1: allowlist ---------------------------------------------
    if s.allowed_user_ids:
        allowed = (user.id in s.allowed_user_ids) or (user.id == s.supervisor_chat_id)
        if not allowed:
            # Tell the user once, then drop silently on retries.
            if user.id not in _denied_notified:
                _denied_notified.add(user.id)
                try:
                    msg = update.effective_message
                    if msg:
                        await msg.reply_text(
                            "🔒 Bot này dành riêng cho một nhóm người dùng được mời.\n\n"
                            f"Nếu bạn muốn được thêm vào, vui lòng gửi `user_id` của bạn ({user.id}) "
                            "cho admin để được cấp quyền.",
                            parse_mode="Markdown",
                        )
                except Exception:
                    pass
            log.info("Access denied for user %s (%s)", user.id, user.full_name)
            raise ApplicationHandlerStop  # stop dispatch entirely

    # --- Gate 2: mandatory onboarding ----------------------------------
    if not s.require_onboarding:
        return

    # Only enforce for users who have already done /start once (they're in DB).
    row = conn().execute("SELECT tz FROM users WHERE tg_id = ?", (user.id,)).fetchone()
    if not row:
        return  # not yet registered; /start handler will create them

    from handlers.onboarding import is_awaiting_tz
    if not is_awaiting_tz(user.id):
        return  # onboarding complete (or skipped)

    # User is in awaiting_tz state. Allow the bypass commands; everything else
    # gets a gentle reminder and is dropped.
    msg = update.effective_message
    text = (msg.text or "").strip() if msg else ""
    first_token = text.split()[0].lower() if text else ""

    if first_token in _ONBOARD_BYPASS_COMMANDS:
        return  # let /start, /help, /tz, /talk_to_human through
    if not text.startswith("/"):
        return  # free-text → goes to qa handler which routes through handle_tz_reply

    # Any other command before tz is set
    try:
        if msg:
            await msg.reply_text(
                "🕐 Mình đang đợi bạn cho biết múi giờ trước.\n"
                "Trả lời tin nhắn trước (vd `Hanoi`, `Tokyo`, `+7`) "
                "hoặc nhắn `skip` để giữ mặc định.",
            )
    except Exception:
        pass
    raise ApplicationHandlerStop
