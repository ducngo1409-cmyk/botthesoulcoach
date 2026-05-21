-- Soul Coach DB schema. Idempotent: safe to run on every boot.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    tg_id      INTEGER PRIMARY KEY,
    name       TEXT,
    tz         TEXT NOT NULL DEFAULT 'Asia/Ho_Chi_Minh',
    joined_at  TEXT NOT NULL DEFAULT (datetime('now')),
    status     TEXT NOT NULL DEFAULT 'active'  -- active|paused|blocked
);

CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(tg_id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    cron_expr    TEXT NOT NULL,              -- standard 5-field cron
    active       INTEGER NOT NULL DEFAULT 1,
    nudge_hours  INTEGER,                    -- NULL = use global REMINDER_NUDGE_HOURS; 0 = no nudge
    max_nudges   INTEGER NOT NULL DEFAULT 1, -- how many follow-up nudges to send
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id, active);

CREATE TABLE IF NOT EXISTS check_ins (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(tg_id) ON DELETE CASCADE,
    sent_at     TEXT NOT NULL DEFAULT (datetime('now')),
    replied_at  TEXT,
    reply_text  TEXT,
    mood        INTEGER,                     -- 1..5
    status      TEXT NOT NULL DEFAULT 'pending'  -- pending|answered|missed
);
CREATE INDEX IF NOT EXISTS idx_checkins_user ON check_ins(user_id, status);
CREATE INDEX IF NOT EXISTS idx_checkins_pending ON check_ins(status, sent_at);

CREATE TABLE IF NOT EXISTS interactions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(tg_id) ON DELETE CASCADE,
    ts           TEXT NOT NULL DEFAULT (datetime('now')),
    direction    TEXT NOT NULL,              -- in|out
    text         TEXT NOT NULL,
    intent       TEXT,                       -- qa|reminder_reply|admin|...
    kb_match_id  INTEGER REFERENCES kb_entries(id) ON DELETE SET NULL,
    llm          INTEGER NOT NULL DEFAULT 0,
    satisfied    INTEGER                     -- NULL|0|1
);
CREATE INDEX IF NOT EXISTS idx_interactions_user_ts ON interactions(user_id, ts);

CREATE TABLE IF NOT EXISTS kb_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT NOT NULL,
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL,
    keywords    TEXT NOT NULL DEFAULT '',    -- comma-separated
    created_by  INTEGER,                     -- supervisor tg_id (NULL = bot auto-promote)
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    hits        INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'active'  -- active|pending (pending = not used in search until approved)
);
CREATE INDEX IF NOT EXISTS idx_kb_category ON kb_entries(category);
-- idx_kb_status created in db._migrate() after ensuring column exists

CREATE TABLE IF NOT EXISTS sessions (
    user_id        INTEGER PRIMARY KEY REFERENCES users(tg_id) ON DELETE CASCADE,
    sat_counter    INTEGER NOT NULL DEFAULT 0,
    last_unsat_at  TEXT,
    current_topic  TEXT,
    escalated_at   TEXT
);

CREATE TABLE IF NOT EXISTS escalations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(tg_id) ON DELETE CASCADE,
    reason        TEXT NOT NULL,             -- kb_miss|counter|manual
    context_json  TEXT NOT NULL,
    sent_to_s_at  TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_escalations_open ON escalations(resolved_at);

CREATE TABLE IF NOT EXISTS reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start   TEXT NOT NULL,
    week_end     TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    sent_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL DEFAULT (datetime('now')),
    actor   INTEGER NOT NULL,                -- tg_id
    action  TEXT NOT NULL,
    target  TEXT
);
