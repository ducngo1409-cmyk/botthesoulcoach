"""Supervisor-only admin commands."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from config import settings
from db import conn, transaction
from handlers import access
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


async def pending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/pending — list users awaiting access approval."""
    if not _is_supervisor(update):
        return
    pending = access.list_pending()
    if not pending:
        await update.message.reply_text("✨ Không có user nào đang chờ duyệt.")
        return
    lines = [f"⏳ *{len(pending)} user đang chờ duyệt*\n"]
    for u in pending:
        lines.append(
            f"• `{u['tg_id']}` — {u['name'] or '?'}\n"
            f"  /approve {u['tg_id']}   /reject {u['tg_id']}\n"
        )
    text = "\n".join(lines)
    if len(text) > 3500:
        text = text[:3500] + "\n…(truncated)"
    await update.message.reply_text(text, parse_mode="Markdown")


async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/approve <user_id> — approve a pending user."""
    if not _is_supervisor(update):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: `/approve <user_id>`", parse_mode="Markdown"
        )
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user_id must be a number.")
        return

    if access.approve_user(target_id):
        await update.message.reply_text(f"✅ Đã duyệt user `{target_id}`.", parse_mode="Markdown")
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=(
                    "✅ Yêu cầu truy cập của bạn đã được chấp nhận!\n\n"
                    "Bắt đầu bằng cách nhắn /start hoặc /help để xem các lệnh."
                ),
            )
        except Exception:
            log.warning("Could not notify approved user %s", target_id)
        log.info("Supervisor approved user %s", target_id)
    else:
        await update.message.reply_text(
            f"User `{target_id}` không ở trạng thái pending (có thể đã duyệt hoặc chưa tồn tại).",
            parse_mode="Markdown",
        )


async def reject_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/reject <user_id> — reject a pending user."""
    if not _is_supervisor(update):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: `/reject <user_id>`", parse_mode="Markdown"
        )
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user_id must be a number.")
        return

    if access.reject_user(target_id):
        await update.message.reply_text(f"🚫 Đã từ chối user `{target_id}`.", parse_mode="Markdown")
        log.info("Supervisor rejected user %s", target_id)
    else:
        await update.message.reply_text(f"User `{target_id}` không tồn tại.", parse_mode="Markdown")


async def user_review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline button on the pending-user DM: ✅ Duyệt / ❌ Từ chối."""
    if not _is_supervisor(update):
        return
    query = update.callback_query
    await query.answer()
    try:
        action, target_id = query.data.split(":")
        target_id = int(target_id)
    except Exception:
        return

    if action == "usr_app":
        ok = access.approve_user(target_id)
        msg = f"✅ Đã duyệt user {target_id}" if ok else f"User {target_id} không pending"
        if ok:
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=(
                        "✅ Yêu cầu truy cập của bạn đã được chấp nhận!\n\n"
                        "Bắt đầu bằng cách nhắn /start hoặc /help để xem các lệnh."
                    ),
                )
            except Exception:
                log.warning("Could not notify approved user %s", target_id)
    else:
        ok = access.reject_user(target_id)
        msg = f"🚫 Đã từ chối user {target_id}" if ok else f"User {target_id} không tồn tại"

    try:
        await query.edit_message_reply_markup(reply_markup=None)
        await query.edit_message_text((query.message.text or "") + f"\n\n{msg}")
    except Exception:
        await context.bot.send_message(settings().supervisor_chat_id, msg)


async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/users [filter] — list users, optionally filtered.

    Filters: pending | approved | rejected | active | paused | blocked
    """
    if not _is_supervisor(update):
        return

    valid_access = {"pending", "approved", "rejected"}
    valid_status = {"active", "paused", "blocked"}
    where = ""
    params: tuple = ()
    filt = (context.args[0].lower() if context.args else "").strip()

    if filt in valid_access:
        where = "WHERE access_status = ?"
        params = (filt,)
        header = f"👥 *Users — {filt}*"
    elif filt in valid_status:
        where = "WHERE status = ?"
        params = (filt,)
        header = f"👥 *Users — {filt}*"
    elif filt and filt != "all":
        await update.message.reply_text(
            "Filter không hợp lệ. Dùng: `pending`, `approved`, `rejected`, "
            "`active`, `paused`, `blocked`, hoặc bỏ trống để xem tất cả.",
            parse_mode="Markdown",
        )
        return
    else:
        header = "👥 *Users (tất cả)*"

    rows = conn().execute(
        f"SELECT tg_id, name, status, access_status, onboarded, joined_at "
        f"FROM users {where} ORDER BY joined_at DESC",
        params,
    ).fetchall()

    if not rows:
        await update.message.reply_text("Không có user nào khớp.")
        return

    access_badge = {"approved": "✅", "pending": "⏳", "rejected": "🚫"}
    status_badge = {"active": "", "paused": " 🔕", "blocked": " ⛔"}
    lines = [f"{header} ({len(rows)})\n"]
    for r in rows:
        ab = access_badge.get(r["access_status"], "?")
        sb = status_badge.get(r["status"], "")
        onboard_mark = "" if r["onboarded"] else " 🕐"
        lines.append(
            f"{ab}{sb}{onboard_mark} `{r['tg_id']}` *{r['name'] or '?'}* — "
            f"joined {r['joined_at'][:10]}"
        )

    text = "\n".join(lines)
    if len(text) > 3500:
        text = text[:3500] + "\n…(truncated)"
    await update.message.reply_text(text, parse_mode="Markdown")


async def user_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/user <id> — detailed profile + stats for a single user."""
    if not _is_supervisor(update):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: `/user <user_id>`", parse_mode="Markdown"
        )
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user_id must be a number.")
        return

    row = conn().execute(
        "SELECT tg_id, name, tz, status, access_status, onboarded, joined_at "
        "FROM users WHERE tg_id = ?",
        (uid,),
    ).fetchone()
    if not row:
        await update.message.reply_text(f"User `{uid}` không tồn tại.", parse_mode="Markdown")
        return

    # Stats
    task_count = conn().execute(
        "SELECT COUNT(*) AS n FROM tasks WHERE user_id = ?", (uid,)
    ).fetchone()["n"]
    inter_count = conn().execute(
        "SELECT COUNT(*) AS n FROM interactions WHERE user_id = ?", (uid,)
    ).fetchone()["n"]
    esc_count = conn().execute(
        "SELECT COUNT(*) AS n FROM escalations WHERE user_id = ?", (uid,)
    ).fetchone()["n"]
    open_esc = conn().execute(
        "SELECT COUNT(*) AS n FROM escalations "
        "WHERE user_id = ? AND resolved_at IS NULL",
        (uid,),
    ).fetchone()["n"]
    mood_row = conn().execute(
        "SELECT AVG(mood) AS avg_mood, COUNT(mood) AS n FROM check_ins "
        "WHERE user_id = ? AND mood IS NOT NULL",
        (uid,),
    ).fetchone()
    last_int = conn().execute(
        "SELECT ts FROM interactions WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (uid,),
    ).fetchone()

    access_badge = {"approved": "✅", "pending": "⏳", "rejected": "🚫"}
    status_badge = {"active": "🟢", "paused": "🔕", "blocked": "⛔"}
    onboard_txt = "✅ done" if row["onboarded"] else "🕐 awaiting tz"

    lines = [
        f"👤 *{row['name'] or '?'}*",
        f"🆔 `{row['tg_id']}`",
        f"🕐 {row['tz']}",
        f"{access_badge.get(row['access_status'], '?')} access: *{row['access_status']}*",
        f"{status_badge.get(row['status'], '?')} status: *{row['status']}*",
        f"📋 onboarding: {onboard_txt}",
        f"📅 joined: {row['joined_at']}",
        "",
        "📊 *Stats*",
        f"• Tasks: {task_count}",
        f"• Interactions: {inter_count}",
        f"• Escalations: {esc_count} ({open_esc} open)",
    ]
    if mood_row["n"]:
        lines.append(f"• Mood: avg {mood_row['avg_mood']:.1f}/5 ({mood_row['n']} ratings)")
    if last_int:
        lines.append(f"• Last interaction: {last_int['ts']}")

    lines.append(
        "\n_Actions:_ `/user_tasks {uid}` `/transcript {uid}` "
        "`/dm {uid} <msg>` `/freeze {uid}` `/revoke {uid}` `/delete_user {uid}`".format(uid=uid)
    )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def user_tasks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/user_tasks <id> — list all reminders for a user."""
    if not _is_supervisor(update):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: `/user_tasks <user_id>`", parse_mode="Markdown"
        )
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user_id must be a number.")
        return
    rows = conn().execute(
        "SELECT id, title, cron_expr, active, nudge_hours, max_nudges "
        "FROM tasks WHERE user_id = ? ORDER BY id",
        (uid,),
    ).fetchall()
    if not rows:
        await update.message.reply_text(f"User `{uid}` chưa có nhắc nhở nào.", parse_mode="Markdown")
        return
    lines = [f"📌 *Tasks của user {uid}* ({len(rows)})\n"]
    for r in rows:
        flag = "✅" if r["active"] else "⏸"
        nudge = ""
        if r["nudge_hours"] == 0 or r["max_nudges"] == 0:
            nudge = " • 🔕 no nudge"
        elif r["nudge_hours"]:
            nudge = f" • nudge {r['nudge_hours']}h"
        lines.append(f"{flag} `#{r['id']}` *{r['title']}* — `{r['cron_expr']}`{nudge}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# --- Access control ------------------------------------------------------

async def revoke_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/revoke <user_id> — take back an approved user's access."""
    if not _is_supervisor(update):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: `/revoke <user_id>`", parse_mode="Markdown"
        )
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user_id must be a number.")
        return
    if uid == settings().supervisor_chat_id:
        await update.message.reply_text("⚠️ Không thể revoke supervisor.")
        return

    with transaction() as cx:
        cur = cx.execute(
            "UPDATE users SET access_status = 'rejected' WHERE tg_id = ?",
            (uid,),
        )
    if cur.rowcount == 0:
        await update.message.reply_text(f"User `{uid}` không tồn tại.", parse_mode="Markdown")
        return
    await update.message.reply_text(f"🚫 Đã thu hồi quyền user `{uid}`.", parse_mode="Markdown")
    try:
        await context.bot.send_message(
            chat_id=uid,
            text="ℹ️ Quyền truy cập của bạn đã bị thu hồi bởi admin.",
        )
    except Exception:
        log.warning("Could not notify revoked user %s", uid)
    log.info("Supervisor revoked user %s", uid)


# --- Operational state (block / freeze) ----------------------------------

async def block_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/block <user_id> — mark user as blocked (bot stops sending to them)."""
    if not _is_supervisor(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: `/block <user_id>`", parse_mode="Markdown")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user_id must be a number.")
        return
    if uid == settings().supervisor_chat_id:
        await update.message.reply_text("⚠️ Không thể block supervisor.")
        return
    with transaction() as cx:
        cur = cx.execute("UPDATE users SET status = 'blocked' WHERE tg_id = ?", (uid,))
    if cur.rowcount == 0:
        await update.message.reply_text(f"User `{uid}` không tồn tại.", parse_mode="Markdown")
        return
    await update.message.reply_text(f"⛔ Đã block user `{uid}`.", parse_mode="Markdown")
    log.info("Supervisor blocked user %s", uid)


async def unblock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/unblock <user_id> — restore status to 'active'."""
    if not _is_supervisor(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: `/unblock <user_id>`", parse_mode="Markdown")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user_id must be a number.")
        return
    with transaction() as cx:
        cur = cx.execute(
            "UPDATE users SET status = 'active' WHERE tg_id = ? AND status = 'blocked'",
            (uid,),
        )
    if cur.rowcount == 0:
        await update.message.reply_text(
            f"User `{uid}` không bị block (hoặc không tồn tại).", parse_mode="Markdown"
        )
        return
    await update.message.reply_text(f"🟢 Đã unblock user `{uid}`.", parse_mode="Markdown")
    log.info("Supervisor unblocked user %s", uid)


async def freeze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/freeze <user_id> — pause all reminders for a user (status='paused')."""
    if not _is_supervisor(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: `/freeze <user_id>`", parse_mode="Markdown")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user_id must be a number.")
        return
    with transaction() as cx:
        cur = cx.execute("UPDATE users SET status = 'paused' WHERE tg_id = ?", (uid,))
    if cur.rowcount == 0:
        await update.message.reply_text(f"User `{uid}` không tồn tại.", parse_mode="Markdown")
        return

    # Pause all their scheduled jobs
    from services.reminders import scheduler
    tasks = conn().execute(
        "SELECT id FROM tasks WHERE user_id = ? AND active = 1", (uid,)
    ).fetchall()
    paused = 0
    for t in tasks:
        try:
            scheduler().pause_job(f"task:{t['id']}:send")
            paused += 1
        except Exception:
            pass
    await update.message.reply_text(
        f"🔕 Đã freeze user `{uid}` ({paused} jobs paused).", parse_mode="Markdown"
    )
    log.info("Supervisor froze user %s (%d jobs)", uid, paused)


async def unfreeze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/unfreeze <user_id> — resume reminders."""
    if not _is_supervisor(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: `/unfreeze <user_id>`", parse_mode="Markdown")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user_id must be a number.")
        return
    with transaction() as cx:
        cur = cx.execute(
            "UPDATE users SET status = 'active' WHERE tg_id = ? AND status = 'paused'",
            (uid,),
        )
    if cur.rowcount == 0:
        await update.message.reply_text(
            f"User `{uid}` không bị freeze (hoặc không tồn tại).", parse_mode="Markdown"
        )
        return

    from services.reminders import scheduler
    tasks = conn().execute(
        "SELECT id FROM tasks WHERE user_id = ? AND active = 1", (uid,)
    ).fetchall()
    resumed = 0
    for t in tasks:
        try:
            scheduler().resume_job(f"task:{t['id']}:send")
            resumed += 1
        except Exception:
            pass
    await update.message.reply_text(
        f"🟢 Đã unfreeze user `{uid}` ({resumed} jobs resumed).", parse_mode="Markdown"
    )
    log.info("Supervisor unfroze user %s (%d jobs)", uid, resumed)


# --- Communication -------------------------------------------------------

async def dm_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/dm <user_id> <message> — admin DMs a user directly through the bot."""
    if not _is_supervisor(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: `/dm <user_id> <message>`\n"
            "Vd: `/dm 12345 Xin chào, mình là coach.`",
            parse_mode="Markdown",
        )
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user_id must be a number.")
        return
    body = " ".join(context.args[1:])
    if not body.strip():
        await update.message.reply_text("Nội dung không được trống.")
        return

    try:
        await context.bot.send_message(
            chat_id=uid,
            text=f"💌 *Tin từ coach:*\n\n{body}",
            parse_mode="Markdown",
        )
        await update.message.reply_text(f"✅ Đã gửi tin cho user `{uid}`.", parse_mode="Markdown")
        log.info("Supervisor DMed user %s: %r", uid, body[:80])
    except Exception as e:
        await update.message.reply_text(f"⚠️ Gửi thất bại: {e}")


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/broadcast <message> — send a message to all approved+active users."""
    if not _is_supervisor(update):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: `/broadcast <message>`\n"
            "Sẽ gửi cho tất cả user *approved* và *active*.",
            parse_mode="Markdown",
        )
        return
    body = " ".join(context.args).strip()
    if not body:
        await update.message.reply_text("Nội dung không được trống.")
        return

    rows = conn().execute(
        "SELECT tg_id FROM users "
        "WHERE access_status = 'approved' AND status = 'active' "
        "AND tg_id != ?",
        (settings().supervisor_chat_id,),
    ).fetchall()

    if not rows:
        await update.message.reply_text("Không có user nào để gửi.")
        return

    sent = 0
    failed = 0
    text = f"📢 *Thông báo từ coach:*\n\n{body}"
    for r in rows:
        try:
            await context.bot.send_message(chat_id=r["tg_id"], text=text, parse_mode="Markdown")
            sent += 1
        except Exception:
            failed += 1
            log.warning("Broadcast to %s failed", r["tg_id"])

    await update.message.reply_text(
        f"📊 Broadcast: gửi thành công {sent}/{len(rows)} (thất bại {failed})."
    )
    log.info("Supervisor broadcast to %d users (%d failed): %r", sent, failed, body[:80])


# --- Lifecycle -----------------------------------------------------------

async def reonboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/reonboard <user_id> — clear onboarded flag, force tz re-prompt."""
    if not _is_supervisor(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: `/reonboard <user_id>`", parse_mode="Markdown")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user_id must be a number.")
        return
    with transaction() as cx:
        cur = cx.execute("UPDATE users SET onboarded = 0 WHERE tg_id = ?", (uid,))
    if cur.rowcount == 0:
        await update.message.reply_text(f"User `{uid}` không tồn tại.", parse_mode="Markdown")
        return
    await update.message.reply_text(
        f"🔄 Đã reset onboarding cho user `{uid}`. Lần nhắn tiếp theo họ sẽ phải set tz lại.",
        parse_mode="Markdown",
    )
    try:
        await context.bot.send_message(
            chat_id=uid,
            text="ℹ️ Admin đã reset thiết lập múi giờ. Vui lòng gõ /start hoặc nhắn tên thành phố để cập nhật.",
        )
    except Exception:
        pass
    log.info("Supervisor re-onboarded user %s", uid)


async def delete_user_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/delete_user <user_id> [confirm] — hard delete user + all related rows."""
    if not _is_supervisor(update):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: `/delete_user <user_id> confirm`\n"
            "⚠️ Hành động này XÓA HẲN user và toàn bộ data (tasks, interactions, "
            "escalations, sessions). Không thể hồi phục.",
            parse_mode="Markdown",
        )
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user_id must be a number.")
        return
    if uid == settings().supervisor_chat_id:
        await update.message.reply_text("⚠️ Không thể xóa supervisor.")
        return

    confirm = (context.args[1] if len(context.args) > 1 else "").lower()
    if confirm != "confirm":
        row = conn().execute("SELECT name FROM users WHERE tg_id = ?", (uid,)).fetchone()
        if not row:
            await update.message.reply_text(f"User `{uid}` không tồn tại.", parse_mode="Markdown")
            return
        await update.message.reply_text(
            f"⚠️ Xác nhận xóa user `{uid}` *{row['name'] or '?'}*?\n"
            f"Gõ lại: `/delete_user {uid} confirm`",
            parse_mode="Markdown",
        )
        return

    # First unschedule any task jobs
    from services.reminders import scheduler
    tasks = conn().execute("SELECT id FROM tasks WHERE user_id = ?", (uid,)).fetchall()
    for t in tasks:
        try:
            scheduler().remove_job(f"task:{t['id']}:send")
        except Exception:
            pass

    # CASCADE deletes will handle tasks/check_ins/interactions/sessions/escalations
    with transaction() as cx:
        cur = cx.execute("DELETE FROM users WHERE tg_id = ?", (uid,))
    if cur.rowcount == 0:
        await update.message.reply_text(f"User `{uid}` không tồn tại.", parse_mode="Markdown")
        return

    # Audit log
    with transaction() as cx:
        cx.execute(
            "INSERT INTO audit_log (actor, action, target) VALUES (?, ?, ?)",
            (update.effective_user.id, "delete_user", str(uid)),
        )
    await update.message.reply_text(
        f"🗑 Đã xóa user `{uid}` và toàn bộ data liên quan.", parse_mode="Markdown"
    )
    log.warning("Supervisor DELETED user %s (with all data)", uid)


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
    """/settask <user_id> | <title> | <giờ> — supervisor assigns task to a user.

    Accepts both friendly time ('daily 22:30') and raw cron ('30 22 * * *').
    """
    if not _is_supervisor(update):
        return
    raw = " ".join(context.args)
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 3:
        await update.message.reply_text(
            "Usage: `/settask <user_id> | <title> | <giờ>`\n"
            "Vd: `/settask 123456789 | Thiền | daily 7:00`\n"
            "Vd: `/settask 123456789 | Họp | weekdays 9:00`",
            parse_mode="Markdown",
        )
        return
    try:
        user_id = int(parts[0])
    except ValueError:
        await update.message.reply_text("user_id must be a number.")
        return
    title, time_expr = parts[1], parts[2]
    if not title or not time_expr:
        await update.message.reply_text("Title and time are both required.")
        return

    user_row = conn().execute("SELECT tg_id FROM users WHERE tg_id = ?", (user_id,)).fetchone()
    if not user_row:
        await update.message.reply_text(
            f"User {user_id} not found. They must /start the bot first."
        )
        return

    from services import timeparser
    cron_expr, summary = timeparser.parse(time_expr)
    if not cron_expr:
        await update.message.reply_text(summary, parse_mode="Markdown")
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
        f"✅ Đã tạo nhắc nhở #{new_id} cho user {user_id}: *{title}*\n"
        f"⏰ {summary}\n`{cron_expr}`",
        parse_mode="Markdown",
    )
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"📌 Coach đã thêm nhắc nhở mới cho bạn: *{title}*\n"
                f"⏰ {summary}\n\n"
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
