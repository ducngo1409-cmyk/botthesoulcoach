"""Soul Coach Telegram bot — entry point.

Usage:
    python main.py

Reads .env, initializes SQLite, registers handlers, starts APScheduler,
and runs the Telegram long-poller.
"""

from __future__ import annotations

import logging
import sys

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

import db
from config import settings
from handlers import access, admin, escalation, onboarding, qa, tasks
from services import reminders
from services.health import start_health_server

log = logging.getLogger(__name__)


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Quiet down chatty libs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def _register_handlers(app: Application) -> None:
    # --- Access gate (allowlist + mandatory onboarding) ---
    # group=-1 runs before everything else; gate raises ApplicationHandlerStop
    # to drop the update entirely if access is denied or onboarding incomplete.
    app.add_handler(TypeHandler(Update, access.gate), group=-1)

    # --- Commands (user) ---
    app.add_handler(CommandHandler("start", onboarding.start))
    app.add_handler(CommandHandler("help", onboarding.help_cmd))
    app.add_handler(CommandHandler("tasks", tasks.list_tasks))
    app.add_handler(CommandHandler("addtask", tasks.add_task))
    app.add_handler(CommandHandler("removetask", tasks.remove_task))
    app.add_handler(CommandHandler("pause", tasks.pause))
    app.add_handler(CommandHandler("resume", tasks.resume))
    app.add_handler(CommandHandler("nudge", tasks.nudge))
    app.add_handler(CommandHandler("tz", onboarding.tz_cmd))
    app.add_handler(CommandHandler("talk_to_human", escalation.talk_to_human))

    # --- Commands (supervisor) ---
    app.add_handler(CommandHandler("report", admin.report_now))
    app.add_handler(CommandHandler("users", admin.users_cmd))
    app.add_handler(CommandHandler("transcript", admin.transcript_cmd))
    app.add_handler(CommandHandler("resolve", escalation.resolve_cmd))
    app.add_handler(CommandHandler("kb_add", admin.kb_add))
    app.add_handler(CommandHandler("kb_list", admin.kb_list))
    app.add_handler(CommandHandler("kb_edit", admin.kb_edit))
    app.add_handler(CommandHandler("kb_del", admin.kb_del))
    app.add_handler(CommandHandler("kb_promote", admin.kb_promote))
    app.add_handler(CommandHandler("kb_pending", admin.kb_pending))
    app.add_handler(CommandHandler("kb_approve", admin.kb_approve))
    app.add_handler(CommandHandler("kb_reject", admin.kb_reject))
    app.add_handler(CommandHandler("settask", admin.settask))
    app.add_handler(CommandHandler("debug", admin.debug_cmd))
    app.add_handler(CommandHandler("pending", admin.pending_cmd))
    app.add_handler(CommandHandler("approve", admin.approve_cmd))
    app.add_handler(CommandHandler("reject", admin.reject_cmd))

    # --- Callback queries ---
    app.add_handler(CallbackQueryHandler(reminders.mood_callback, pattern=r"^mood:"))
    app.add_handler(CallbackQueryHandler(qa.feedback_callback, pattern=r"^sat:"))
    app.add_handler(CallbackQueryHandler(escalation.resolve_callback, pattern=r"^resolve:"))
    app.add_handler(CallbackQueryHandler(admin.kb_review_callback, pattern=r"^kb_(app|rej):"))
    app.add_handler(CallbackQueryHandler(admin.user_review_callback, pattern=r"^usr_(app|rej):"))

    # --- Free-text messages (must be last) ---
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, qa.on_user_message))


async def _post_init(app: Application) -> None:
    """Start the APScheduler after the Application is up."""
    await reminders.start_scheduler(app)
    log.info("Soul Coach is up and running")


async def _post_shutdown(app: Application) -> None:
    await reminders.stop_scheduler()
    db.close()


def main() -> None:
    s = settings()
    _setup_logging(s.log_level)

    db.init_db()
    start_health_server(port=s.health_port)

    app = (
        ApplicationBuilder()
        .token(s.telegram_token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    _register_handlers(app)

    log.info("Starting bot polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
