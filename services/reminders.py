"""Proactive reminder scheduling.

APScheduler runs in-process. On startup we re-arm jobs from the `tasks` table
and recover orphan check-ins. Each scheduled job:

  1. Sends a check-in DM with a 5-emoji mood keyboard.
  2. Inserts a row in `check_ins` with status='pending'.
  3. Two follow-up jobs are armed:
       - nudge at REMINDER_NUDGE_HOURS
       - mark missed at REMINDER_MISS_HOURS
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import Forbidden, TelegramError
from telegram.ext import Application

from config import settings
from db import conn, transaction

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

MOOD_EMOJI = ["😣", "😕", "😐", "🙂", "😄"]


def scheduler() -> AsyncIOScheduler:
    if _scheduler is None:
        raise RuntimeError("Scheduler not started")
    return _scheduler


def _job_id_send(task_id: int) -> str:
    return f"task:{task_id}:send"


def _job_id_nudge(checkin_id: int) -> str:
    return f"checkin:{checkin_id}:nudge"


def _job_id_miss(checkin_id: int) -> str:
    return f"checkin:{checkin_id}:miss"


def _mood_keyboard(checkin_id: int) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(MOOD_EMOJI[i], callback_data=f"mood:{checkin_id}:{i + 1}")
        for i in range(5)
    ]
    return InlineKeyboardMarkup([buttons])


# --- Job actions ---------------------------------------------------------

async def _send_checkin(app: Application, task_id: int, user_id: int, title: str) -> None:
    # Skip if user paused/blocked, OR this specific task was paused via /pause <id>
    row = conn().execute(
        "SELECT status FROM users WHERE tg_id = ?", (user_id,)
    ).fetchone()
    if not row or row["status"] != "active":
        log.info("Skip check-in for user %s (status=%s)", user_id, row["status"] if row else "missing")
        return
    t_row = conn().execute(
        "SELECT active FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if not t_row or not t_row["active"]:
        log.info("Skip check-in: task %s is paused", task_id)
        return

    text = (
        f"🌱 *Check-in: {title}*\n\n"
        "Hôm nay thế nào rồi? Nhắn vài dòng nếu muốn, rồi chọn cảm xúc của bạn nhé 👇"
    )

    with transaction() as cx:
        cur = cx.execute(
            "INSERT INTO check_ins (task_id, user_id) VALUES (?, ?)",
            (task_id, user_id),
        )
        checkin_id = cur.lastrowid

    try:
        await app.bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=_mood_keyboard(checkin_id),
        )
    except Forbidden:
        log.warning("User %s blocked the bot — marking blocked", user_id)
        with transaction() as cx:
            cx.execute("UPDATE users SET status = 'blocked' WHERE tg_id = ?", (user_id,))
        return
    except TelegramError as e:
        log.exception("Failed to send check-in to %s: %s", user_id, e)
        return

    # Arm follow-ups — respect per-task config
    s = settings()
    task_cfg = conn().execute(
        "SELECT nudge_hours, max_nudges FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    nudge_hours = (task_cfg["nudge_hours"] if task_cfg and task_cfg["nudge_hours"] is not None
                   else s.reminder_nudge_hours)
    max_nudges = task_cfg["max_nudges"] if task_cfg else 1

    if max_nudges > 0 and nudge_hours > 0:
        nudge_at = datetime.now(timezone.utc) + timedelta(hours=nudge_hours)
        scheduler().add_job(
            _send_nudge, DateTrigger(run_date=nudge_at),
            args=[app, checkin_id, user_id, title],
            id=_job_id_nudge(checkin_id), replace_existing=True,
        )

    miss_at = datetime.now(timezone.utc) + timedelta(hours=s.reminder_miss_hours)
    scheduler().add_job(
        _mark_missed, DateTrigger(run_date=miss_at),
        args=[checkin_id, user_id],
        id=_job_id_miss(checkin_id), replace_existing=True,
    )


async def _send_nudge(app: Application, checkin_id: int, user_id: int, title: str) -> None:
    row = conn().execute(
        "SELECT status FROM check_ins WHERE id = ?", (checkin_id,)
    ).fetchone()
    if not row or row["status"] != "pending":
        return
    try:
        await app.bot.send_message(
            chat_id=user_id,
            text=f"⏰ Nhắc nhẹ thôi — vẫn còn kịp check-in *{title}* đó. Không ép đâu 🌿",
            parse_mode="Markdown",
        )
    except TelegramError as e:
        log.warning("Nudge failed for %s: %s", user_id, e)


async def _mark_missed(checkin_id: int, user_id: int) -> None:
    with transaction() as cx:
        cx.execute(
            "UPDATE check_ins SET status = 'missed' WHERE id = ? AND status = 'pending'",
            (checkin_id,),
        )
    log.info("Marked check-in %s as missed", checkin_id)


# --- Public API ----------------------------------------------------------

def schedule_task_job(app: Application, task_id: int, user_id: int,
                      title: str, cron_expr: str) -> None:
    """Add or replace the cron job for a task."""
    s = settings()
    user_row = conn().execute(
        "SELECT tz FROM users WHERE tg_id = ?", (user_id,)
    ).fetchone()
    tz = user_row["tz"] if user_row else s.default_tz

    trigger = CronTrigger.from_crontab(cron_expr, timezone=tz)
    scheduler().add_job(
        _send_checkin, trigger,
        args=[app, task_id, user_id, title],
        id=_job_id_send(task_id), replace_existing=True,
        misfire_grace_time=600,
    )
    log.info("Scheduled task %s for user %s @ %s (%s)", task_id, user_id, cron_expr, tz)


def unschedule_task_job(app: Application, task_id: int) -> None:
    try:
        scheduler().remove_job(_job_id_send(task_id))
    except Exception:
        pass


def _recover_orphans() -> None:
    """Mark pending check-ins past the miss-window as missed."""
    s = settings()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=s.reminder_miss_hours)
    with transaction() as cx:
        cx.execute(
            "UPDATE check_ins SET status = 'missed' "
            "WHERE status = 'pending' AND sent_at < ?",
            (cutoff.strftime("%Y-%m-%d %H:%M:%S"),),
        )


async def start_scheduler(app: Application) -> None:
    """Start scheduler, re-arm task jobs, and arm the weekly report."""
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = AsyncIOScheduler()
    _scheduler.start()

    _recover_orphans()

    # Re-arm all active tasks
    rows = conn().execute(
        "SELECT t.id, t.user_id, t.title, t.cron_expr "
        "FROM tasks t JOIN users u ON u.tg_id = t.user_id "
        "WHERE t.active = 1 AND u.status != 'blocked'"
    ).fetchall()
    for r in rows:
        try:
            schedule_task_job(app, r["id"], r["user_id"], r["title"], r["cron_expr"])
        except Exception:
            log.exception("Failed to schedule task %s", r["id"])

    # Weekly report
    s = settings()
    from services.reports import send_weekly_report
    _scheduler.add_job(
        send_weekly_report, CronTrigger.from_crontab(s.report_cron, timezone=s.default_tz),
        args=[app], id="weekly_report", replace_existing=True,
        misfire_grace_time=3600,
    )
    log.info("Scheduler started; %d task jobs armed", len(rows))


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


# --- Mood callback handler ----------------------------------------------

async def mood_callback(update, context):
    """Handle taps on the mood inline keyboard."""
    query = update.callback_query
    await query.answer()
    try:
        _, checkin_id, mood = query.data.split(":")
        checkin_id, mood = int(checkin_id), int(mood)
    except Exception:
        return

    with transaction() as cx:
        cx.execute(
            "UPDATE check_ins SET mood = ?, replied_at = datetime('now'), "
            "status = CASE WHEN status = 'pending' THEN 'answered' ELSE status END "
            "WHERE id = ?",
            (mood, checkin_id),
        )
    await query.edit_message_text(
        f"{query.message.text}\n\n_Đã ghi nhận cảm xúc: {MOOD_EMOJI[mood - 1]}_",
        parse_mode="Markdown",
    )
