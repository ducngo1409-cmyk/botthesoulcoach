"""Unit tests — no real Telegram or Gemini calls.

Tests added by the v2.1 TODO sweep:
  - Crisis-keyword pre-filter
  - /health endpoint HTTP response
  - Timezone onboarding prompt flow
  - Reminder _mark_missed + _send_checkin skip-paused
  - Pause/resume DB flag + scheduler job calls

Run with:
    python -m tests.test_unit          # from project root
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# --- Bootstrap env before any project imports --------------------------------
os.environ.setdefault("TELEGRAM_TOKEN", "test:dummy")
os.environ.setdefault("SUPERVISOR_CHAT_ID", "1")
os.environ.setdefault("GEMINI_API_KEY", "dummy")

_tmp = Path(tempfile.mkdtemp(prefix="soulcoach_unit_"))
os.environ["DB_PATH"] = str(_tmp / "unit.db")

_project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_project_root))

import db
db.init_db()

# Insert a default user that most tests rely on.
with db.transaction() as _cx:
    _cx.execute("INSERT OR IGNORE INTO users (tg_id, name) VALUES (?, ?)", (42, "UnitTestUser"))
    _cx.execute("INSERT OR IGNORE INTO tasks (user_id, title, cron_expr) VALUES (?, ?, ?)",
                (42, "Morning run", "0 7 * * *"))

# Fetch the task id for user 42
_task_row = db.conn().execute("SELECT id FROM tasks WHERE user_id = 42").fetchone()
_TASK_ID_42 = _task_row["id"] if _task_row else 1


# --- Helpers -----------------------------------------------------------------

def _mock_update(user_id: int = 42, text: str = "hello") -> MagicMock:
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.full_name = "Unit Tester"
    update.effective_user.first_name = "Unit"
    update.message = AsyncMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


def _mock_context() -> MagicMock:
    ctx = MagicMock()
    ctx.bot = AsyncMock()
    ctx.application = MagicMock()
    ctx.args = []
    return ctx


# =============================================================================
# 1. Crisis-keyword filter
# =============================================================================

class TestCrisisFilter(unittest.TestCase):
    def test_crisis_keywords_detected(self):
        from handlers.qa import _is_crisis
        assert _is_crisis("I want to kill myself")
        assert _is_crisis("I'm feeling suicidal")
        assert _is_crisis("thinking about self-harm")
        assert _is_crisis("tôi muốn chết rồi")

    def test_non_crisis_not_detected(self):
        from handlers.qa import _is_crisis
        assert not _is_crisis("I can't focus today")
        assert not _is_crisis("feeling a bit sad")
        assert not _is_crisis("meditation isn't working")


class TestCrisisHandler(unittest.IsolatedAsyncioTestCase):
    async def test_crisis_message_returns_safe_reply_no_llm(self):
        from handlers.qa import on_user_message

        update = _mock_update(text="I want to kill myself")
        ctx = _mock_context()

        with patch("services.llm.soft_reply") as mock_llm, \
             patch("services.kb.search", return_value=[]):
            await on_user_message(update, ctx)
            mock_llm.assert_not_called()

        update.message.reply_text.assert_called_once()
        reply_text = update.message.reply_text.call_args[0][0]
        assert "💙" in reply_text
        assert "1800 599 920" in reply_text

    async def test_non_crisis_proceeds_to_kb(self):
        from handlers.qa import on_user_message

        update = _mock_update(text="I have trouble sleeping")
        ctx = _mock_context()

        with patch("services.kb.search", return_value=[]) as mock_kb, \
             patch("services.llm.soft_reply", return_value="Try a sleep routine."):
            await on_user_message(update, ctx)
            mock_kb.assert_called_once()

        update.message.reply_text.assert_called_once()
        reply_text = update.message.reply_text.call_args[0][0]
        assert "💙" not in reply_text


# =============================================================================
# 2. /health endpoint
# =============================================================================

class TestHealthEndpoint(unittest.TestCase):
    def test_health_returns_200_ok(self):
        import time
        import urllib.request
        from services.health import start_health_server

        start_health_server(port=19080)
        time.sleep(0.15)  # let thread start

        resp = urllib.request.urlopen("http://localhost:19080/health")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.read(), b"ok")

    def test_unknown_path_returns_404(self):
        import urllib.error
        import urllib.request
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen("http://localhost:19080/notexist")
        self.assertEqual(cm.exception.code, 404)


# =============================================================================
# 3. Timezone onboarding prompt
# =============================================================================

class TestTimezonePrompt(unittest.IsolatedAsyncioTestCase):
    async def test_new_user_sets_awaiting_tz_flag(self):
        from handlers import onboarding

        user_id = 8001
        update = _mock_update(user_id=user_id)
        update.effective_user.full_name = "TZ Newbie"
        update.effective_user.first_name = "TZ"
        ctx = _mock_context()

        await onboarding.start(update, ctx)
        self.assertTrue(onboarding.is_awaiting_tz(user_id))
        # Two messages sent: welcome + tz prompt
        self.assertEqual(update.message.reply_text.call_count, 2)

    async def test_returning_user_no_tz_prompt(self):
        from handlers import onboarding

        # Insert as already-onboarded user
        with db.transaction() as cx:
            cx.execute(
                "INSERT OR IGNORE INTO users (tg_id, name, onboarded) VALUES (?, ?, 1)",
                (8002, "Old User"),
            )

        update = _mock_update(user_id=8002)
        update.effective_user.full_name = "Old User"
        ctx = _mock_context()

        await onboarding.start(update, ctx)
        self.assertFalse(onboarding.is_awaiting_tz(8002))
        self.assertEqual(update.message.reply_text.call_count, 1)

    async def test_valid_tz_reply_updates_db_and_marks_onboarded(self):
        from handlers import onboarding

        user_id = 8003
        with db.transaction() as cx:
            cx.execute(
                "INSERT OR IGNORE INTO users (tg_id, name, onboarded) VALUES (?, ?, 0)",
                (user_id, "TZ Setter"),
            )

        update = _mock_update(user_id=user_id, text="America/New_York")
        ctx = _mock_context()

        handled = await onboarding.handle_tz_reply(update, ctx)
        self.assertTrue(handled)
        self.assertFalse(onboarding.is_awaiting_tz(user_id))
        row = db.conn().execute("SELECT tz, onboarded FROM users WHERE tg_id = ?", (user_id,)).fetchone()
        self.assertEqual(row["tz"], "America/New_York")
        self.assertEqual(row["onboarded"], 1)

    async def test_invalid_tz_keeps_user_in_awaiting_state(self):
        """v2.7.2: invalid tz reply keeps user in awaiting state via DB so they can retry."""
        from handlers import onboarding

        user_id = 8004
        with db.transaction() as cx:
            cx.execute(
                "INSERT OR IGNORE INTO users (tg_id, name, onboarded) VALUES (?, ?, 0)",
                (user_id, "Bad TZ"),
            )

        update = _mock_update(user_id=user_id, text="not/a/timezone!!!")
        ctx = _mock_context()

        handled = await onboarding.handle_tz_reply(update, ctx)
        self.assertTrue(handled)
        # User stays in awaiting state — onboarded should still be 0
        self.assertTrue(onboarding.is_awaiting_tz(user_id))
        update.message.reply_text.assert_called_once()
        warning = update.message.reply_text.call_args[0][0]
        self.assertIn("⚠️", warning)

    async def test_explicit_skip_marks_onboarded(self):
        """v2.7.2: only 'skip'/'bỏ qua' keywords mark onboarded."""
        from handlers import onboarding

        user_id = 8005
        with db.transaction() as cx:
            cx.execute(
                "INSERT OR IGNORE INTO users (tg_id, name, onboarded) VALUES (?, ?, 0)",
                (user_id, "Skipper"),
            )

        update = _mock_update(user_id=user_id, text="skip")
        ctx = _mock_context()
        handled = await onboarding.handle_tz_reply(update, ctx)
        self.assertTrue(handled)
        self.assertFalse(onboarding.is_awaiting_tz(user_id))

    async def test_khong_does_not_skip_onboarding(self):
        """v2.7.1 regression: 'không' is a common word and must NOT trigger skip."""
        from handlers import onboarding

        user_id = 8006
        with db.transaction() as cx:
            cx.execute(
                "INSERT OR IGNORE INTO users (tg_id, name, onboarded) VALUES (?, ?, 0)",
                (user_id, "Khong"),
            )

        update = _mock_update(user_id=user_id, text="không")
        ctx = _mock_context()
        await onboarding.handle_tz_reply(update, ctx)
        # Should stay in awaiting state and get a retry prompt
        self.assertTrue(onboarding.is_awaiting_tz(user_id))

    async def test_state_survives_simulated_restart(self):
        """v2.7.2 regression: onboarding state must persist in DB.

        Previously stored in an in-memory _awaiting_tz set, which was lost on
        bot restart — leaving users unable to complete onboarding.
        """
        from handlers import onboarding

        user_id = 8007
        with db.transaction() as cx:
            cx.execute(
                "INSERT OR IGNORE INTO users (tg_id, name, onboarded) VALUES (?, ?, 0)",
                (user_id, "Restart Survivor"),
            )

        # Simulate restart by clearing any module-level caches (there shouldn't be any now).
        # The DB row alone should be sufficient to identify the user's state.
        self.assertTrue(onboarding.is_awaiting_tz(user_id))

        # User completes onboarding after the "restart"
        update = _mock_update(user_id=user_id, text="Hanoi")
        ctx = _mock_context()
        handled = await onboarding.handle_tz_reply(update, ctx)
        self.assertTrue(handled)
        self.assertFalse(onboarding.is_awaiting_tz(user_id))

    async def test_random_garbage_doesnt_corrupt_state(self):
        """v2.7.2 regression: garbage input must keep the user retry-able forever."""
        from handlers import onboarding

        user_id = 8008
        with db.transaction() as cx:
            cx.execute(
                "INSERT OR IGNORE INTO users (tg_id, name, onboarded) VALUES (?, ?, 0)",
                (user_id, "Garbage Typer"),
            )

        ctx = _mock_context()
        # 10 garbage messages in a row
        for garbage in ["...", "@@@", "?", "abc", "lol", "wtf", "  ", "x", "null", "@$%"]:
            update = _mock_update(user_id=user_id, text=garbage)
            await onboarding.handle_tz_reply(update, ctx)
            self.assertTrue(
                onboarding.is_awaiting_tz(user_id),
                f"User was kicked out of awaiting state by garbage input {garbage!r}",
            )

        # Finally a valid input works
        update = _mock_update(user_id=user_id, text="Tokyo")
        await onboarding.handle_tz_reply(update, ctx)
        self.assertFalse(onboarding.is_awaiting_tz(user_id))
        row = db.conn().execute("SELECT tz FROM users WHERE tg_id = ?", (user_id,)).fetchone()
        self.assertEqual(row["tz"], "Asia/Tokyo")


# =============================================================================
# 4. Reminder core — _mark_missed, _send_checkin (paused skip)
# =============================================================================

class TestReminderCore(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with db.transaction() as cx:
            cx.execute("INSERT OR IGNORE INTO users (tg_id, name) VALUES (?, ?)", (100, "Reminder User"))
            cx.execute("INSERT OR IGNORE INTO tasks (user_id, title, cron_expr) VALUES (?, ?, ?)",
                       (100, "Daily check", "0 9 * * *"))
        self._task_id = db.conn().execute(
            "SELECT id FROM tasks WHERE user_id = 100"
        ).fetchone()["id"]

    async def test_mark_missed_updates_status(self):
        from services.reminders import _mark_missed

        with db.transaction() as cx:
            cur = cx.execute(
                "INSERT INTO check_ins (task_id, user_id) VALUES (?, ?)",
                (self._task_id, 100),
            )
            checkin_id = cur.lastrowid

        await _mark_missed(checkin_id, 100)
        row = db.conn().execute(
            "SELECT status FROM check_ins WHERE id = ?", (checkin_id,)
        ).fetchone()
        self.assertEqual(row["status"], "missed")

    async def test_mark_missed_does_not_overwrite_answered(self):
        from services.reminders import _mark_missed

        with db.transaction() as cx:
            cur = cx.execute(
                "INSERT INTO check_ins (task_id, user_id, status) VALUES (?, ?, 'answered')",
                (self._task_id, 100),
            )
            checkin_id = cur.lastrowid

        await _mark_missed(checkin_id, 100)
        row = db.conn().execute(
            "SELECT status FROM check_ins WHERE id = ?", (checkin_id,)
        ).fetchone()
        self.assertEqual(row["status"], "answered")  # unchanged

    async def test_send_checkin_skips_paused_user(self):
        from services.reminders import _send_checkin

        with db.transaction() as cx:
            cx.execute(
                "INSERT OR IGNORE INTO users (tg_id, name, status) VALUES (?, ?, 'paused')",
                (101, "Paused"),
            )
            cx.execute(
                "INSERT OR IGNORE INTO tasks (user_id, title, cron_expr) VALUES (?, ?, ?)",
                (101, "T", "0 8 * * *"),
            )
        task_id = db.conn().execute(
            "SELECT id FROM tasks WHERE user_id = 101"
        ).fetchone()["id"]

        app = MagicMock()
        app.bot = AsyncMock()

        await _send_checkin(app, task_id, 101, "T")
        app.bot.send_message.assert_not_called()

    async def test_send_checkin_creates_db_row_for_active_user(self):
        from services.reminders import _send_checkin

        with db.transaction() as cx:
            cx.execute(
                "INSERT OR IGNORE INTO users (tg_id, name, status) VALUES (?, ?, 'active')",
                (102, "Active"),
            )
            cx.execute(
                "INSERT OR IGNORE INTO tasks (user_id, title, cron_expr) VALUES (?, ?, ?)",
                (102, "T2", "0 8 * * *"),
            )
        task_id = db.conn().execute(
            "SELECT id FROM tasks WHERE user_id = 102"
        ).fetchone()["id"]

        app = MagicMock()
        app.bot = AsyncMock()

        mock_sched = MagicMock()
        with patch("services.reminders.scheduler", return_value=mock_sched):
            await _send_checkin(app, task_id, 102, "T2")

        app.bot.send_message.assert_called_once()
        row = db.conn().execute(
            "SELECT status FROM check_ins WHERE task_id = ?", (task_id,)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "pending")


# =============================================================================
# 5. Pause / resume — DB flag + scheduler job calls
# =============================================================================

class TestPauseResume(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with db.transaction() as cx:
            cx.execute(
                "INSERT OR IGNORE INTO users (tg_id, name, status) VALUES (?, ?, 'active')",
                (200, "Pausable"),
            )
            cx.execute(
                "INSERT OR IGNORE INTO tasks (user_id, title, cron_expr) VALUES (?, ?, ?)",
                (200, "Habit", "0 7 * * *"),
            )

    async def test_pause_sets_db_status_and_pauses_jobs(self):
        from handlers.tasks import pause

        mock_sched = MagicMock()
        update = _mock_update(user_id=200)
        ctx = _mock_context()

        with patch("services.reminders.scheduler", return_value=mock_sched):
            await pause(update, ctx)

        row = db.conn().execute(
            "SELECT status FROM users WHERE tg_id = 200"
        ).fetchone()
        self.assertEqual(row["status"], "paused")
        mock_sched.pause_job.assert_called()

    async def test_resume_sets_db_status_and_resumes_jobs(self):
        from handlers.tasks import resume

        # Ensure user is paused first
        with db.transaction() as cx:
            cx.execute("UPDATE users SET status = 'paused' WHERE tg_id = 200")

        mock_sched = MagicMock()
        update = _mock_update(user_id=200)
        ctx = _mock_context()

        with patch("services.reminders.scheduler", return_value=mock_sched):
            await resume(update, ctx)

        row = db.conn().execute(
            "SELECT status FROM users WHERE tg_id = 200"
        ).fetchone()
        self.assertEqual(row["status"], "active")
        mock_sched.resume_job.assert_called()


# =============================================================================
# Entry point
# =============================================================================

def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
