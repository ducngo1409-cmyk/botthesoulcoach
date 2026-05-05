"""Supervisor-only admin commands."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from config import settings
from db import conn, transaction
from services import kb

log = logging.getLogger(__name__)


def _is_supervisor(update: Update) -> bool:
    s = settings()
    return (
        update.effective_user is not None
        and update.effective_user.id == s.supervisor_chat_id
    )


async def debug_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/debug — supervisor-only live status snapshot."""
    if not _is_supervisor(update):
        return

    users = conn().execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    escalated = conn().execute(
        "SELECT user_id FROM sessions WHERE escalated_at IS NOT NULL"
    ).fetchall()
    kb_count = conn().execute("SELECT COUNT(*) AS n FROM kb_entries WHERE status = 'active'").fetchone()["n"]
    kb_pending_count = conn().execute("SELECT COUNT(*) AS n FROM kb_entries WHERE status = 'pending'").fetchone()["n"]
    open_esc = conn().execute(
        "SELECT user_id, reason, sent_to_s_at FROM escalations "
        "WHERE resolved_at IS NULL ORDER BY sent_to_s_at DESC LIMIT 5"
    ).fetchall()
    recent_errors = conn().execute(
        "SELECT ts, text FROM interactions WHERE direction='out' AND llm=1 "
        "ORDER BY id DESC LIMIT 3"
    ).fetchall()

    lines = ["🔧 *Bot Debug Snapshot*\n"]
    lines.append(f"👥 Users: {users}")
    lines.append(f"📚 KB active: {kb_count} | pending: {kb_pending_count}")
    lines.append(f"🔴 Escalated sessions: {len(escalated)}")
    if escalated:
        for r in escalated:
            lines.append(f"   • uid {r['user_id']}")

    lines.append(f"\n🚨 Open escalations ({len(open_esc)}):")
    if open_esc:
        for r in open_esc:
            lines.append(f"   • uid {r['user_id']} | {r['reason']} | {r['sent_to_s_at']}")
    else:
        lines.append("   (none)")

    lines.append("\n💬 Recent LLM replies:")
    if recent_errors:
        for r in recent_errors:
            lines.append(f"   {r['ts']}: {r['text'][:60]}…")
    else:
        lines.append("   (none)")

    lines.append(
        "\n_/resolve <uid> to clear escalation_\n"
        "_tail -f logs/bot.err.log to monitor live_"
    )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def report_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_supervisor(update):
        return
    from services.reports import send_weekly_report
    await update.message.reply_text("Generating report…")
    await send_weekly_report(context.application)


async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_supervisor(update):
        return
    rows = conn().execute(
        "SELECT tg_id, name, status, joined_at FROM users ORDER BY joined_at"
    ).fetchall()
    if not rows:
        await update.message.reply_text("No users registered yet.")
        return
    lines = ["👥 *Users*"]
    for r in rows:
        lines.append(f"• `{r['tg_id']}` *{r['name'] or '?'}* — {r['status']} — joined {r['joined_at']}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def transcript_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/transcript <user_id> [YYYY-WW]"""
    if not _is_supervisor(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: `/transcript <user_id> [YYYY-WW]`",
                                        parse_mode="Markdown")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user_id must be a number.")
        return
    week = context.args[1] if len(context.args) > 1 else None

    if week:
        try:
            year, wk = week.split("-")
            year, wk = int(year), int(wk)
        except Exception:
            await update.message.reply_text("Week must be YYYY-WW (e.g. 2026-18).")
            return
        rows = conn().execute(
            "SELECT direction, ts, text FROM interactions "
            "WHERE user_id = ? AND strftime('%Y', ts) = ? "
            "AND CAST(strftime('%W', ts) AS INTEGER) = ? "
            "ORDER BY id",
            (user_id, str(year), wk),
        ).fetchall()
    else:
        rows = conn().execute(
            "SELECT direction, ts, text FROM interactions "
            "WHERE user_id = ? ORDER BY id DESC LIMIT 50",
            (user_id,),
        ).fetchall()
        rows = list(reversed(rows))

    # Audit log
    with transaction() as cx:
        cx.execute(
            "INSERT INTO audit_log (actor, action, target) VALUES (?, ?, ?)",
            (update.effective_user.id, "transcript", str(user_id)),
        )

    if not rows:
        await update.message.reply_text("No interactions found.")
        return

    chunk = []
    size = 0
    for r in rows:
        who = "👤" if r["direction"] == "in" else "🤖"
        line = f"{who} {r['ts']}\n{r['text']}\n"
        if size + len(line) > 3500:
            await update.message.reply_text("".join(chunk))
            chunk, size = [], 0
        chunk.append(line)
        size += len(line)
    if chunk:
        await update.message.reply_text("".join(chunk))


# --- KB management -------------------------------------------------------

async def kb_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/kb_add <category> | <question> | <answer> | <keywords>"""
    if not _is_supervisor(update):
        return
    raw = " ".join(context.args)
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 3:
        await update.message.reply_text(
            "Usage: `/kb_add <category> | <question> | <answer> | <keywords>`",
            parse_mode="Markdown",
        )
        return
    while len(parts) < 4:
        parts.append("")
    cat, q, a, kw = parts[:4]
    if not (cat and q and a):
        await update.message.reply_text("category, question, and answer are required.")
        return
    new_id = kb.add(cat, q, a, kw, created_by=update.effective_user.id)
    await update.message.reply_text(f"✅ Added KB entry #{new_id}.")


async def kb_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_supervisor(update):
        return
    cat = context.args[0] if context.args else None
    entries = kb.list_all(cat)
    if not entries:
        await update.message.reply_text("KB is empty.")
        return
    chunk, size = [], 0
    header = f"📚 KB ({len(entries)} entries{f', cat={cat}' if cat else ''}):\n\n"
    chunk.append(header)
    size = len(header)
    for e in entries:
        line = f"#{e.id} [{e.category}] {e.question}\n  → {e.answer[:80]}{'…' if len(e.answer) > 80 else ''}\n"
        if size + len(line) > 3500:
            await update.message.reply_text("".join(chunk))
            chunk, size = [], 0
        chunk.append(line)
        size += len(line)
    if chunk:
        await update.message.reply_text("".join(chunk))


async def kb_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/kb_edit <id> <field>=<value>  (field: category|question|answer|keywords)"""
    if not _is_supervisor(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: `/kb_edit <id> <field>=<value>`", parse_mode="Markdown"
        )
        return
    try:
        entry_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("id must be a number.")
        return
    rest = " ".join(context.args[1:])
    if "=" not in rest:
        await update.message.reply_text("Need field=value.")
        return
    field, value = rest.split("=", 1)
    field = field.strip()
    value = value.strip()
    if field not in {"category", "question", "answer", "keywords"}:
        await update.message.reply_text("Allowed fields: category, question, answer, keywords.")
        return
    ok = kb.edit(entry_id, **{field: value})
    await update.message.reply_text("✅ Updated." if ok else "Not found.")


async def kb_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_supervisor(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: `/kb_del <id>`", parse_mode="Markdown")
        return
    try:
        entry_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("id must be a number.")
        return
    ok = kb.delete(entry_id)
    await update.message.reply_text("🗑 Deleted." if ok else "Not found.")


async def settask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/settask <user_id> | <title> | <cron> — supervisor assigns task to a user."""
    if not _is_supervisor(update):
        return
    raw = " ".join(context.args)
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 3:
        await update.message.reply_text(
            "Usage: `/settask <user_id> | <title> | <cron>`\n"
            "Example: `/settask 123456789 | Thiền buổi sáng | 0 8 * * *`",
            parse_mode="Markdown",
        )
        return
    try:
        user_id = int(parts[0])
    except ValueError:
        await update.message.reply_text("user_id must be a number.")
        return
    title, cron_expr = parts[1], parts[2]
    if not title or not cron_expr:
        await update.message.reply_text("Title and cron are both required.")
        return

    user_row = conn().execute("SELECT tg_id FROM users WHERE tg_id = ?", (user_id,)).fetchone()
    if not user_row:
        await update.message.reply_text(f"User {user_id} not found. They must /start the bot first.")
        return

    from apscheduler.triggers.cron import CronTrigger
    try:
        CronTrigger.from_crontab(cron_expr)
    except Exception:
        await update.message.reply_text(
            "Invalid cron. Must be 5 fields: `min hour dom month dow`",
            parse_mode="Markdown",
        )
        return

    with transaction() as cx:
        cur = cx.execute(
            "INSERT INTO tasks (user_id, title, cron_expr) VALUES (?, ?, ?)",
            (user_id, title, cron_expr),
        )
        new_id = cur.lastrowid

    from services.reminders import schedule_task_job
    schedule_task_job(context.application, new_id, user_id, title, cron_expr)

    await update.message.reply_text(
        f"✅ Đã tạo nhắc nhở #{new_id} cho user {user_id}: *{title}* — `{cron_expr}`",
        parse_mode="Markdown",
    )
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"📌 Coach đã thêm nhắc nhở mới cho bạn: *{title}*\n"
                f"Lịch: `{cron_expr}`\n\n"
                "Nhắn /tasks để xem tất cả nhắc nhở."
            ),
            parse_mode="Markdown",
        )
    except Exception:
        log.warning("Could not notify user %s about new task", user_id)


async def kb_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all pending KB entries awaiting supervisor approval."""
    if not _is_supervisor(update):
        return
    pending = kb.list_pending()
    if not pending:
        await update.message.reply_text("✨ Không có entry nào đang chờ duyệt.")
        return
    lines = [f"📋 *KB pending ({len(pending)})*\n"]
    for e in pending:
        lines.append(
            f"#{e.id} — {e.question[:80]}\n"
            f"  → {e.answer[:80]}{'…' if len(e.answer) > 80 else ''}\n"
            f"  /kb\\_approve {e.id}    /kb\\_reject {e.id}\n"
        )
    text = "\n".join(lines)
    if len(text) > 3500:
        text = text[:3500] + "\n…(truncated)"
    await update.message.reply_text(text, parse_mode="Markdown")


async def kb_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/kb_approve <id> [category] [keywords] — promote a pending entry."""
    if not _is_supervisor(update):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: `/kb_approve <id> [category] [keywords]`", parse_mode="Markdown"
        )
        return
    try:
        entry_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("id must be a number.")
        return
    category = context.args[1] if len(context.args) > 1 else None
    keywords = " ".join(context.args[2:]) if len(context.args) > 2 else None
    ok = kb.approve(entry_id, category=category, keywords=keywords)
    await update.message.reply_text(
        f"✅ Approved KB #{entry_id}." if ok else "Not found or already active."
    )


async def kb_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/kb_reject <id> — delete a pending entry."""
    if not _is_supervisor(update):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: `/kb_reject <id>`", parse_mode="Markdown"
        )
        return
    try:
        entry_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("id must be a number.")
        return
    ok = kb.delete(entry_id)
    await update.message.reply_text(
        f"🗑 Rejected KB #{entry_id}." if ok else "Not found."
    )


async def kb_review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline button on pending-KB notification: ✅ Approve / ❌ Reject."""
    if not _is_supervisor(update):
        return
    query = update.callback_query
    await query.answer()
    try:
        action, entry_id = query.data.split(":")
        entry_id = int(entry_id)
    except Exception:
        return
    if action == "kb_app":
        ok = kb.approve(entry_id)
        msg = f"✅ Approved KB #{entry_id}" if ok else f"KB #{entry_id} not pending"
    else:
        ok = kb.delete(entry_id)
        msg = f"🗑 Rejected KB #{entry_id}" if ok else f"KB #{entry_id} not found"
    try:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.edit_message_text(query.message.text + f"\n\n{msg}")
    except Exception:
        await context.bot.send_message(settings().supervisor_chat_id, msg)


async def kb_promote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/kb_promote <interaction_id> — promote a successful LLM reply to a KB entry.

    The matching incoming user message (interaction_id - 1, or the last 'in'
    before the LLM reply) becomes the question; the bot reply becomes the answer.
    Supervisor is then nudged to set category/keywords via /kb_edit.
    """
    if not _is_supervisor(update):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: `/kb_promote <interaction_id>`", parse_mode="Markdown"
        )
        return
    try:
        iid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("interaction_id must be a number.")
        return

    bot_msg = conn().execute(
        "SELECT id, user_id, text, llm, direction FROM interactions WHERE id = ?",
        (iid,),
    ).fetchone()
    if not bot_msg or bot_msg["direction"] != "out" or not bot_msg["llm"]:
        await update.message.reply_text(
            "That interaction isn't an LLM-generated bot reply."
        )
        return

    user_msg = conn().execute(
        "SELECT text FROM interactions "
        "WHERE user_id = ? AND id < ? AND direction = 'in' "
        "ORDER BY id DESC LIMIT 1",
        (bot_msg["user_id"], iid),
    ).fetchone()
    question = user_msg["text"] if user_msg else "(unknown)"

    new_id = kb.add(
        category="general",
        question=question,
        answer=bot_msg["text"],
        keywords="",
        created_by=update.effective_user.id,
    )
    await update.message.reply_text(
        f"✅ Promoted to KB #{new_id}.\n"
        f"Refine with `/kb_edit {new_id} category=<cat>` "
        f"and `/kb_edit {new_id} keywords=<kw1, kw2>`",
        parse_mode="Markdown",
    )
