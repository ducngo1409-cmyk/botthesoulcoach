"""Escalation handlers: triggers + supervisor notification + /talk_to_human.

Root cause of silent failures: Telegram Markdown v1 rejects messages that
contain unescaped * _ ` [ ] characters in user-supplied text. We now use
parse_mode="HTML" throughout and escape all user content with html.escape().
"""

from __future__ import annotations

import html
import json
import logging
from typing import Literal

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import settings
from db import conn, transaction
from services import satisfaction

log = logging.getLogger(__name__)

Reason = Literal["kb_miss", "counter", "manual"]

_REASON_LABEL = {
    "kb_miss": "🔎 Knowledge gap (no good KB match)",
    "counter": "🔁 5 unsatisfied responses in a row",
    "manual": "🙋 User asked for a human",
}


def _last_n_turns_html(user_id: int, n: int = 5) -> str:
    """Return last N interactions as HTML-safe text."""
    rows = conn().execute(
        "SELECT direction, text, ts FROM interactions "
        "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, n),
    ).fetchall()
    rows = list(reversed(rows))
    if not rows:
        return "<i>(no prior interactions)</i>"
    out = []
    for r in rows:
        who = "👤" if r["direction"] == "in" else "🤖"
        snippet = html.escape(r["text"][:200]) + ("…" if len(r["text"]) > 200 else "")
        ts = html.escape(str(r["ts"]))
        out.append(f"{who} <code>{ts}</code>  {snippet}")
    return "\n".join(out)


def _user_label_html(user_id: int) -> str:
    row = conn().execute(
        "SELECT name FROM users WHERE tg_id = ?", (user_id,)
    ).fetchone()
    name = html.escape(row["name"] if row else "?")
    return f"{name} (uid <code>{user_id}</code>)"


async def escalate(
    context: ContextTypes.DEFAULT_TYPE, user_id: int, reason: Reason
) -> None:
    """Notify S, mark user escalated, tell user a human is coming."""
    s = settings()

    if satisfaction.is_escalated(user_id):
        log.debug("User %s already escalated; skipping duplicate", user_id)
        # Tell user they're already in queue instead of silently ignoring
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="⏳ Bạn đang trong hàng chờ coach rồi — họ sẽ liên hệ bạn sớm nhé!",
            )
        except Exception:
            pass
        return

    satisfaction.mark_escalated(user_id)

    context_payload = {
        "user_id": user_id,
        "reason": reason,
        "last_turns": [
            {"direction": r["direction"], "ts": r["ts"], "text": r["text"]}
            for r in conn().execute(
                "SELECT direction, ts, text FROM interactions "
                "WHERE user_id = ? ORDER BY id DESC LIMIT 5",
                (user_id,),
            ).fetchall()
        ],
    }
    with transaction() as cx:
        cx.execute(
            "INSERT INTO escalations (user_id, reason, context_json) VALUES (?, ?, ?)",
            (user_id, reason, json.dumps(context_payload, ensure_ascii=False)),
        )

    # Build supervisor notification using HTML (safe against user-supplied text)
    text = (
        "🚨 <b>Escalation</b>\n"
        f"User: {_user_label_html(user_id)}\n"
        f"Reason: {html.escape(_REASON_LABEL[reason])}\n\n"
        "<b>Last turns:</b>\n"
        f"{_last_n_turns_html(user_id)}\n\n"
        f"Tap <b>Mark resolved</b> or send <code>/resolve {user_id}</code> once handled."
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Mark resolved", callback_data=f"resolve:{user_id}"),
    ]])
    # Fan out to all handlers (admins + coachers)
    from services import roles
    recipients = roles.get_ids_with_perm("handle_escalation")
    sent_to = 0
    for rid in recipients:
        try:
            await context.bot.send_message(
                chat_id=rid,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            sent_to += 1
        except Exception:
            log.warning("Failed to notify %s about escalation for user %s", rid, user_id)
    log.info("Escalation for user %s (%s) sent to %d handler(s)", user_id, reason, sent_to)

    # Tell user
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "Mình muốn bạn nhận được hỗ trợ đúng nhất. "
                "Mình đã kết nối với coach con người — họ sẽ liên hệ bạn sớm. 🤝"
            ),
        )
    except Exception:
        log.exception("Failed to notify user %s of escalation", user_id)


async def talk_to_human(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    await escalate(context, user.id, reason="manual")


async def resolve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Supervisor command: /resolve <user_id>"""
    s = settings()
    if update.effective_user is None or update.effective_user.id != s.supervisor_chat_id:
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/resolve &lt;user_id&gt;</code>", parse_mode="HTML"
        )
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user_id must be a number.")
        return

    with transaction() as cx:
        cx.execute(
            "UPDATE escalations SET resolved_at = datetime('now') "
            "WHERE user_id = ? AND resolved_at IS NULL",
            (user_id,),
        )
    satisfaction.clear_escalation(user_id)

    await update.message.reply_text(f"✅ Escalation for {user_id} resolved.")
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="✨ Mình đã sẵn sàng hỗ trợ bạn trở lại. Bạn cần gì cứ nhắn nhé.",
        )
    except Exception:
        pass


async def resolve_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline 'Mark resolved' button on escalation cards."""
    s = settings()
    query = update.callback_query
    await query.answer()
    if update.effective_user is None or update.effective_user.id != s.supervisor_chat_id:
        return
    try:
        _, user_id = query.data.split(":")
        user_id = int(user_id)
    except Exception:
        return

    with transaction() as cx:
        cx.execute(
            "UPDATE escalations SET resolved_at = datetime('now') "
            "WHERE user_id = ? AND resolved_at IS NULL",
            (user_id,),
        )
    satisfaction.clear_escalation(user_id)
    await query.edit_message_reply_markup(reply_markup=None)
    await context.bot.send_message(
        s.supervisor_chat_id, f"✅ Resolved for user {user_id}."
    )
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="✨ Mình đã sẵn sàng hỗ trợ bạn trở lại. Bạn cần gì cứ nhắn nhé.",
        )
    except Exception:
        pass
