"""SQLite connection + schema bootstrap.

We use a single shared connection in WAL mode. python-telegram-bot is async but
SQLite operations here are short and synchronous; this is fine for the expected
load (single-digit users, few writes/sec).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from config import settings

log = logging.getLogger(__name__)

_conn: sqlite3.Connection | None = None
_lock = threading.RLock()


def init_db() -> None:
    """Open connection, apply schema, run KB seed if needed."""
    global _conn
    s = settings()

    _conn = sqlite3.connect(
        s.db_path,
        check_same_thread=False,
        detect_types=sqlite3.PARSE_DECLTYPES,
        isolation_level=None,  # autocommit; we manage transactions explicitly
    )
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA foreign_keys = ON;")

    schema_path = s.project_root / "schema.sql"
    with schema_path.open("r", encoding="utf-8") as f:
        _conn.executescript(f.read())

    log.info("DB initialized at %s", s.db_path)
    _migrate()
    _maybe_seed_kb()
    _clear_stale_escalations()


def _migrate() -> None:
    """Idempotent column-adding migrations for existing DBs."""
    # kb_entries.status (v2.6)
    cur = conn().execute("PRAGMA table_info(kb_entries)")
    cols = {row[1] for row in cur.fetchall()}
    if "status" not in cols:
        conn().execute(
            "ALTER TABLE kb_entries ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
        )
        log.info("Migration: added kb_entries.status column")
    conn().execute("CREATE INDEX IF NOT EXISTS idx_kb_status ON kb_entries(status)")

    # tasks.nudge_hours / max_nudges (v2.7)
    cur = conn().execute("PRAGMA table_info(tasks)")
    tcols = {row[1] for row in cur.fetchall()}
    if "nudge_hours" not in tcols:
        conn().execute("ALTER TABLE tasks ADD COLUMN nudge_hours INTEGER")
        log.info("Migration: added tasks.nudge_hours column")
    if "max_nudges" not in tcols:
        conn().execute("ALTER TABLE tasks ADD COLUMN max_nudges INTEGER NOT NULL DEFAULT 1")
        log.info("Migration: added tasks.max_nudges column")


def _clear_stale_escalations() -> None:
    """On startup: auto-resolve escalations open > 24h — prevents users being stuck forever."""
    cutoff = conn().execute(
        "SELECT datetime('now', '-24 hours')"
    ).fetchone()[0]
    rows = conn().execute(
        "SELECT user_id FROM escalations WHERE resolved_at IS NULL AND sent_to_s_at < ?",
        (cutoff,),
    ).fetchall()
    for row in rows:
        uid = row[0]
        with transaction() as cx:
            cx.execute(
                "UPDATE escalations SET resolved_at = datetime('now') "
                "WHERE user_id = ? AND resolved_at IS NULL",
                (uid,),
            )
            cx.execute(
                "UPDATE sessions SET escalated_at = NULL, sat_counter = 0 WHERE user_id = ?",
                (uid,),
            )
        log.info("Auto-cleared stale escalation for user %s (>24h)", uid)


def _maybe_seed_kb() -> None:
    """Load kb_seed.yaml on first run only (if kb_entries is empty)."""
    import yaml  # local import — only needed at startup

    s = settings()
    seed_path = s.project_root / "kb_seed.yaml"
    if not seed_path.exists():
        log.info("No kb_seed.yaml present; skipping seed")
        return

    cur = conn().execute("SELECT COUNT(*) AS n FROM kb_entries")
    n = cur.fetchone()["n"]
    if n > 0:
        log.info("KB already has %d entries; skipping seed", n)
        return

    with seed_path.open("r", encoding="utf-8") as f:
        entries = yaml.safe_load(f) or []

    with transaction() as cx:
        for e in entries:
            cx.execute(
                "INSERT INTO kb_entries (category, question, answer, keywords) "
                "VALUES (?, ?, ?, ?)",
                (e["category"], e["question"], e["answer"], e.get("keywords", "")),
            )
    log.info("Seeded %d KB entries", len(entries))


def conn() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("DB not initialized; call init_db() first")
    return _conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """BEGIN / COMMIT or ROLLBACK. Acquires the module-level lock."""
    cx = conn()
    with _lock:
        cx.execute("BEGIN")
        try:
            yield cx
            cx.execute("COMMIT")
        except Exception:
            cx.execute("ROLLBACK")
            raise


def close() -> None:
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None
