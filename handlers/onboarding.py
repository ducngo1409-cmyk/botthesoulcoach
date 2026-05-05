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
            f"✅ Timezone set to *{text}*. Your reminders will fire in local time.",
            parse_mode="Markdown",
        )
        log.info("User %s set timezone to %s", user.id, text)
    except Exception:
        await update.message.reply_text(
            f"⚠️ I don't recognise *{text}* as a timezone — "
            f"sticking with the default. You can always DM me a valid tz name later.",
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
            f"👋 Hi {user.first_name}, I'm your Soul Coach.\n\n"
            "I'll check in on you for the things you want to stay on top of, "
            "and answer questions whenever you need a hand.\n\n"
            "Try:\n"
            "• /addtask Morning meditation | 0 8 * * *\n"
            "• /tasks — see your reminders\n"
            "• /help — full command list\n"
            "• Or just message me anything that's on your mind."
        )
        await update.message.reply_text(
            "🕐 *What timezone are you in?*\n\n"
            f"Common choices: {_COMMON_TZ}\n\n"
            "_Reply with any valid tz name, e.g. `Asia/Ho_Chi_Minh`. "
            "Just ignore this to keep the default._",
            parse_mode="Markdown",
        )
        _awaiting_tz.add(user.id)
    else:
        await update.message.reply_text("👋 Welcome back. Type /help to see what I can do.")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    is_supervisor = (
        update.effective_user is not None
        and update.effective_user.id == settings().supervisor_chat_id
    )

    user_cmds = (
        "📋 *User commands*\n"
        "/start — register\n"
        "/tasks — list your reminders\n"
        "/addtask <title> | <cron> — add reminder (cron is 5-field)\n"
        "/removetask <id> — remove\n"
        "/pause — mute reminders\n"
        "/resume — unmute reminders\n"
        "/talk\\_to\\_human — connect to a coach\n\n"
        "_Or just type anything; I'll try to help._"
    )
    sup_cmds = (
        "\n\n👤 *Supervisor commands*\n"
        "/users — list active users\n"
        "/report — send weekly report now\n"
        "/resolve <user\\_id> — close an escalation\n"
        "/transcript <user\\_id> \\[YYYY-WW] — view verbatim history\n"
        "/kb\\_add <cat> | <q> | <a> | <kw>\n"
        "/kb\\_list \\[cat]\n"
        "/kb\\_edit <id> <field>=<value>\n"
        "/kb\\_del <id>\n"
        "/kb\\_promote <interaction\\_id>"
    )
    text = user_cmds + (sup_cmds if is_supervisor else "")
    await update.message.reply_text(text, parse_mode="Markdown")
