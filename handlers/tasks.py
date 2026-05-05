"""Task (reminder) management commands."""

from __future__ import annotations

import logging

from apscheduler.triggers.cron import CronTrigger
from telegram import Update
from telegram.ext import ContextTypes

from db import conn, transaction

log = logging.getLogger(__name__)


def _validate_cron(expr: str) -> bool:
    """Return True if `expr` is a valid 5-field cron."""
    try:
        CronTrigger.from_crontab(expr)
        return True
    except Exception:
        return False


async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    rows = conn().execute(
        "SELECT id, title, cron_expr, active FROM tasks "
        "WHERE user_id = ? ORDER BY id",
        (user.id,),
    ).fetchall()
    if not rows:
        await update.message.reply_text(
            "Bạn chưa có nhắc nhở nào.\n"
            "Thêm bằng `/addtask <tên> | <cron>`",
            parse_mode="Markdown",
        )
        return

    lines = ["📌 *Nhắc nhở của bạn*"]
    for r in rows:
        flag = "✅" if r["active"] else "⏸"
        lines.append(f"{flag} `#{r['id']}` *{r['title']}* — `{r['cron_expr']}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not context.args:
        await update.message.reply_text(
            "Cách dùng: `/addtask <tên> | <cron>`\n"
            "Ví dụ: `/addtask Thiền buổi sáng | 0 8 * * *`",
            parse_mode="Markdown",
        )
        return
    raw = " ".join(context.args)
    if "|" not in raw:
        await update.message.reply_text(
            "Thiếu dấu `|`. Thử `/addtask Thiền buổi sáng | 0 8 * * *`",
            parse_mode="Markdown",
        )
        return
    title, cron_expr = (s.strip() for s in raw.split("|", 1))
    if not title or not cron_expr:
        await update.message.reply_text("Cần có cả tên và biểu thức cron.")
        return
    if not _validate_cron(cron_expr):
        await update.message.reply_text(
            "Biểu thức cron không hợp lệ. Cần đúng 5 trường.\n"
            "Gợi nhớ: `phút giờ ngày tháng thứ`\n"
            "• Mỗi ngày 8 giờ sáng: `0 8 * * *`\n"
            "• T2/T4/T6 lúc 7 tối: `0 19 * * 1,3,5`",
            parse_mode="Markdown",
        )
        return

    with transaction() as cx:
        cur = cx.execute(
            "INSERT INTO tasks (user_id, title, cron_expr) VALUES (?, ?, ?)",
            (user.id, title, cron_expr),
        )
        new_id = cur.lastrowid

    # Schedule it on the running scheduler
    from services.reminders import schedule_task_job
    schedule_task_job(context.application, new_id, user.id, title, cron_expr)

    await update.message.reply_text(
        f"✅ Đã thêm nhắc nhở #{new_id}: *{title}* — `{cron_expr}`",
        parse_mode="Markdown",
    )


async def remove_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Cách dùng: `/removetask <id>`", parse_mode="Markdown")
        return
    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID phải là số.")
        return

    with transaction() as cx:
        cur = cx.execute(
            "DELETE FROM tasks WHERE id = ? AND user_id = ?",
            (task_id, user.id),
        )
        ok = cur.rowcount > 0

    if not ok:
        await update.message.reply_text("Không tìm thấy nhắc nhở này.")
        return

    from services.reminders import unschedule_task_job
    unschedule_task_job(context.application, task_id)
    await update.message.reply_text(f"🗑 Đã xóa nhắc nhở #{task_id}.")


async def pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    with transaction() as cx:
        cx.execute("UPDATE users SET status = 'paused' WHERE tg_id = ?", (user.id,))

    # Actually pause the APScheduler jobs so they don't fire while paused.
    from services.reminders import scheduler
    task_rows = conn().execute(
        "SELECT id FROM tasks WHERE user_id = ? AND active = 1", (user.id,)
    ).fetchall()
    for row in task_rows:
        try:
            scheduler().pause_job(f"task:{row['id']}:send")
        except Exception:
            pass  # job may not exist if scheduler was just restarted

    await update.message.reply_text("🔕 Đã tắt nhắc nhở. Nhắn /resume để bật lại.")


async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    with transaction() as cx:
        cx.execute("UPDATE users SET status = 'active' WHERE tg_id = ?", (user.id,))

    # Re-activate the APScheduler jobs.
    from services.reminders import scheduler
    task_rows = conn().execute(
        "SELECT id FROM tasks WHERE user_id = ? AND active = 1", (user.id,)
    ).fetchall()
    for row in task_rows:
        try:
            scheduler().resume_job(f"task:{row['id']}:send")
        except Exception:
            pass  # job may not exist; harmless

    await update.message.reply_text("🔔 Nhắc nhở đã được bật lại.")
