"""Task (reminder) management commands.

User-friendly time parsing: see services/timeparser.py. Accepts both raw cron
expressions and natural-language forms ("daily 22:30", "every 6 hours",
"weekdays 9:00").

Per-task pause/resume: `/pause`, `/resume`, `/pause <id>`, `/resume <id>`.

Per-task nudge config: `/nudge <id> <hours>` where hours=0 disables nudges.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from db import conn, transaction
from services import timeparser

log = logging.getLogger(__name__)


# --- /tasks --------------------------------------------------------------

async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    rows = conn().execute(
        "SELECT id, title, cron_expr, active, nudge_hours, max_nudges "
        "FROM tasks WHERE user_id = ? ORDER BY id",
        (user.id,),
    ).fetchall()
    if not rows:
        await update.message.reply_text(
            "Bạn chưa có nhắc nhở nào.\n"
            "Thêm bằng `/addtask <tên> | <giờ>`\n"
            "Vd: `/addtask Thiền | daily 7:00`",
            parse_mode="Markdown",
        )
        return

    lines = ["📌 *Nhắc nhở của bạn*"]
    for r in rows:
        flag = "✅" if r["active"] else "⏸"
        nudge_txt = ""
        if r["nudge_hours"] == 0 or r["max_nudges"] == 0:
            nudge_txt = " • 🔕 không nhắc lại"
        elif r["nudge_hours"]:
            nudge_txt = f" • nhắc lại sau {r['nudge_hours']}h"
        lines.append(
            f"{flag} `#{r['id']}` *{r['title']}* — `{r['cron_expr']}`{nudge_txt}"
        )
    lines.append("\n_Dùng `/pause <id>` hoặc `/resume <id>` cho từng nhắc._")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# --- /addtask ------------------------------------------------------------

async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not context.args:
        await update.message.reply_text(
            "Cách dùng: `/addtask <tên> | <giờ>`\n\n"
            "🕐 *Đơn giản*\n"
            "`/addtask Thiền | daily 7:00`\n"
            "`/addtask Uống nước | every 3 hours`\n"
            "`/addtask Báo cáo | weekdays 17:30`\n"
            "`/addtask Đi bộ | weekends 6:30`\n"
            "`/addtask Họp | every monday 9:00`\n\n"
            "🕑 *Cron 5 trường (nâng cao)*\n"
            "`/addtask Thiền | 0 7 * * *`",
            parse_mode="Markdown",
        )
        return

    raw = " ".join(context.args)
    if "|" not in raw:
        await update.message.reply_text(
            "Thiếu dấu `|`. Cú pháp: `/addtask <tên> | <giờ>`\n"
            "Vd: `/addtask Thiền | daily 7:00`",
            parse_mode="Markdown",
        )
        return

    title, time_expr = (s.strip() for s in raw.split("|", 1))
    if not title or not time_expr:
        await update.message.reply_text("Cần có cả tên và thời gian.")
        return

    cron_expr, summary = timeparser.parse(time_expr)
    if not cron_expr:
        await update.message.reply_text(summary, parse_mode="Markdown")
        return

    with transaction() as cx:
        cur = cx.execute(
            "INSERT INTO tasks (user_id, title, cron_expr) VALUES (?, ?, ?)",
            (user.id, title, cron_expr),
        )
        new_id = cur.lastrowid

    from services.reminders import schedule_task_job
    schedule_task_job(context.application, new_id, user.id, title, cron_expr)

    await update.message.reply_text(
        f"✅ Đã thêm nhắc nhở #{new_id}: *{title}*\n"
        f"⏰ {summary}\n"
        f"`{cron_expr}`",
        parse_mode="Markdown",
    )


# --- /removetask ---------------------------------------------------------

async def remove_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not context.args:
        await update.message.reply_text(
            "Cách dùng: `/removetask <id>`", parse_mode="Markdown"
        )
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


# --- /pause [id] ---------------------------------------------------------

async def pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    from services.reminders import scheduler

    # Per-task pause: /pause <id>
    if context.args:
        try:
            task_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("ID phải là số. Vd: `/pause 3`", parse_mode="Markdown")
            return

        row = conn().execute(
            "SELECT id, title FROM tasks WHERE id = ? AND user_id = ?",
            (task_id, user.id),
        ).fetchone()
        if not row:
            await update.message.reply_text(f"Không tìm thấy nhắc nhở #{task_id}.")
            return

        with transaction() as cx:
            cx.execute("UPDATE tasks SET active = 0 WHERE id = ?", (task_id,))
        try:
            scheduler().pause_job(f"task:{task_id}:send")
        except Exception:
            pass
        await update.message.reply_text(
            f"🔕 Đã tắt nhắc nhở #{task_id}: *{row['title']}*.\n"
            f"Bật lại bằng `/resume {task_id}`.",
            parse_mode="Markdown",
        )
        return

    # No arg: pause all (legacy behavior)
    with transaction() as cx:
        cx.execute("UPDATE users SET status = 'paused' WHERE tg_id = ?", (user.id,))
    task_rows = conn().execute(
        "SELECT id FROM tasks WHERE user_id = ? AND active = 1", (user.id,)
    ).fetchall()
    for r in task_rows:
        try:
            scheduler().pause_job(f"task:{r['id']}:send")
        except Exception:
            pass
    await update.message.reply_text(
        f"🔕 Đã tắt *tất cả* {len(task_rows)} nhắc nhở. Nhắn /resume để bật lại "
        "hoặc /pause <id> để tắt từng nhắc.",
        parse_mode="Markdown",
    )


# --- /resume [id] --------------------------------------------------------

async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    from services.reminders import scheduler

    if context.args:
        try:
            task_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("ID phải là số. Vd: `/resume 3`", parse_mode="Markdown")
            return

        row = conn().execute(
            "SELECT id, title FROM tasks WHERE id = ? AND user_id = ?",
            (task_id, user.id),
        ).fetchone()
        if not row:
            await update.message.reply_text(f"Không tìm thấy nhắc nhở #{task_id}.")
            return

        with transaction() as cx:
            cx.execute("UPDATE tasks SET active = 1 WHERE id = ?", (task_id,))
        try:
            scheduler().resume_job(f"task:{task_id}:send")
        except Exception:
            pass
        await update.message.reply_text(
            f"🔔 Đã bật lại nhắc nhở #{task_id}: *{row['title']}*.",
            parse_mode="Markdown",
        )
        return

    # No arg: resume all
    with transaction() as cx:
        cx.execute("UPDATE users SET status = 'active' WHERE tg_id = ?", (user.id,))
    task_rows = conn().execute(
        "SELECT id FROM tasks WHERE user_id = ?", (user.id,)
    ).fetchall()
    resumed = 0
    for r in task_rows:
        try:
            scheduler().resume_job(f"task:{r['id']}:send")
            resumed += 1
        except Exception:
            pass
    await update.message.reply_text(f"🔔 Đã bật lại {resumed} nhắc nhở.")


# --- /nudge <id> <hours> -------------------------------------------------

async def nudge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set per-task nudge interval. Hours = 0 disables follow-up nudges."""
    user = update.effective_user
    if len(context.args) < 2:
        await update.message.reply_text(
            "Cách dùng: `/nudge <task_id> <hours>`\n"
            "• `/nudge 3 6` — nhắc lại sau 6 giờ\n"
            "• `/nudge 3 0` — tắt nhắc lại (chỉ ping 1 lần)\n"
            "_Mặc định: dùng giá trị global REMINDER\\_NUDGE\\_HOURS._",
            parse_mode="Markdown",
        )
        return
    try:
        task_id = int(context.args[0])
        hours = int(context.args[1])
    except ValueError:
        await update.message.reply_text("ID và hours phải là số.")
        return
    if hours < 0 or hours > 168:
        await update.message.reply_text("Hours phải trong khoảng 0-168 (1 tuần).")
        return

    row = conn().execute(
        "SELECT id, title FROM tasks WHERE id = ? AND user_id = ?",
        (task_id, user.id),
    ).fetchone()
    if not row:
        await update.message.reply_text(f"Không tìm thấy nhắc nhở #{task_id}.")
        return

    max_nudges = 0 if hours == 0 else 1
    with transaction() as cx:
        cx.execute(
            "UPDATE tasks SET nudge_hours = ?, max_nudges = ? WHERE id = ?",
            (hours if hours > 0 else None, max_nudges, task_id),
        )
    msg = (
        f"🔕 Tắt nhắc lại cho #{task_id} *{row['title']}*."
        if hours == 0
        else f"⏰ Nhắc lại sau {hours} giờ cho #{task_id} *{row['title']}*."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")
