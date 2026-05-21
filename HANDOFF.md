# Soul Coach Bot — Session Handoff (2026-05-21, v2.10.1)

> Reading order for the next session:
> **1. This file** (5 min) — current state, what to do first
> **2. AUDIT.md** (10 min) — known limitations + roadmap
> **3. SPEC.md** (10 min) — design source of truth
> **4. ADMIN_GUIDE.md** §0 + §2 — RBAC roles + user mgmt
> Skip USER_GUIDE / TESTPLAN unless touching those areas.

## 1. Current version: v2.10.1

| Layer | Tech | Status |
|---|---|---|
| Telegram | python-telegram-bot v21+ async | ✅ stable |
| Hosting | GCP e2-micro free tier, `soul-coach` (us-central1-a) | ✅ active |
| DB | SQLite WAL, 10 tables, in-process migration | ✅ stable |
| Scheduler | APScheduler AsyncIOScheduler | ✅ stable, in-process |
| LLM | Gemini Flash (2.5-lite primary, 4-model failover) | ✅ stable |
| Tests | 14 smoke + 21 unit (35 total) | ✅ all pass |
| CI | GitHub Actions `.github/workflows/ci.yml` | ✅ on every push |
| Monitoring | UptimeRobot HTTP `:8080/health` | ✅ active |

## 2. What works (feature inventory)

### User-facing
- `/start` request-to-join with admin approval
- Onboarding: city/country/offset → IANA timezone (60+ aliases)
- `/tz`, `/tasks`, `/addtask` (friendly time + cron), `/removetask`
- `/pause [id]`, `/resume [id]`, `/nudge <id> <hours>` per-task control
- `/talk_to_human` manual escalation
- Free chat → KB hit → LLM fallback → offline empathy if all fail
- Mood-emoji check-ins with 12h nudge + 24h missed
- Crisis-keyword pre-filter (EN+VI)

### Admin (👑) / Coacher (🎓) / Service (⚙️) / User (👤)
- RBAC with 12 permissions, explicit matrix (see services/roles.py)
- 16+ admin commands: user mgmt, KB mgmt, escalation handling, communication, lifecycle, role mgmt
- Max 2 admins (env `MAX_ADMINS`)
- Auto fan-out: pending users → admins; escalations & KB pending → admins+coachers
- Role-aware `/help` shows only what user can use

### Infrastructure
- Logrotate weekly, 4-week retention
- Off-host backup via rclone (cron)
- Idempotent migrations on every boot (4 columns added so far:
  `kb_entries.status`, `tasks.nudge_hours+max_nudges`, `users.onboarded`,
  `users.access_status`, `users.role`)

## 3. Where things live

```
config.py                       env loader + Settings dataclass
db.py                           init_db(), _migrate(), conn(), transaction()
schema.sql                      10 tables (idempotent)
main.py                         handler registration + scheduler bootstrap

services/
  roles.py        ← v2.10  permission matrix + role helpers + admin cap
  llm.py                   model+key failover, 5xx/empty/network → next attempt
  kb.py                    cached fuzzy retrieval, status filter, dedup gate
  satisfaction.py          EN+VI regex sentiment + counter + escalated_at
  reminders.py             APScheduler + per-task nudge config
  timeparser.py            "daily 22:30" → "30 22 * * *"
  tz_aliases.py            "Hanoi" → "Asia/Ho_Chi_Minh"
  reports.py               weekly markdown + JSON
  health.py                /health HTTP daemon

handlers/
  access.py       ← v2.7.2+v2.8  strict state-machine gate (group=-1)
  onboarding.py            /start, /tz, /help (role-aware), tz reply handler
  tasks.py                 /addtask /removetask /pause /resume /nudge /tasks
  qa.py                    crisis → KB → LLM → offline empathy + auto-promote
  escalation.py            /talk_to_human, fan-out to admins+coachers
  admin.py                 all admin/coacher/service commands (29 handlers)
```

## 4. Known limitations (see AUDIT.md for full)

- Single SQLite connection → write contention above ~50/s
- KB fuzzy search O(N) → noticeable above 5k entries
- Sync LLM calls block the handler coroutine (other users unaffected though)
- APScheduler in-process → single point of failure
- No 2FA on admin role
- Secrets in plaintext `.env`
- No multi-instance support (would fire duplicate reminders)

Capacity ceiling at current setup: **~500 DAU comfortable, 2k tight, 10k+ needs rewrite.**

## 5. Open work / next priorities

From AUDIT §7 roadmap:

**v2.11 (1-2 days)** — Observability
- Structured JSON logging
- Sentry SDK
- `/debug` self-check (scheduler-alive, DB-writable, last LLM)

**v2.12 (2-3 days)** — User delight
- `/snooze`, `/mood`, `/export`, `/forget`
- "Skip today" button on check-ins
- DST transition warnings

**v2.13 (1-2 days)** — Admin polish
- Paginated `/users`
- `/stats`, `/audit_log` viewer
- Bulk approval
- Audit every admin command

## 6. Quick start in a new session

```bash
# 1. Pull latest, see what changed
cd /Users/dustinngo/Project/Bot_The_Soul_Coach
git log --oneline -5

# 2. Verify tests pass
source .venv/bin/activate
python -m tests.test_smoke && python -m tests.test_unit

# 3. Check VM is healthy
gcloud compute ssh soul-coach --zone us-central1-a \
  --command="sudo systemctl is-active soul-coach"

# 4. Recent log activity
gcloud compute ssh soul-coach --zone us-central1-a \
  --command="sudo tail -30 /home/hallo_5ambloom/Bot_The_Soul_Coach/logs/bot.err.log"

# 5. Check live DB state (users + KB count)
gcloud compute ssh soul-coach --zone us-central1-a \
  --command="sudo sqlite3 /home/hallo_5ambloom/Bot_The_Soul_Coach/data/soul_coach.db \
  'SELECT tg_id, name, role, access_status FROM users; \
   SELECT COUNT(*) FROM kb_entries WHERE status=\"active\"'"
```

## 7. Don't break these invariants

When making changes, preserve:

1. **State persistence** — never store onboarding/approval/escalated state in module variables; always DB-backed
2. **Failover chain** — LLM call must continue on 429/5xx/empty/network errors; only raise after all attempts
3. **Bot must never go silent** — offline empathy fallback in qa.py covers the all-fail case
4. **Bootstrap admin** — `SUPERVISOR_CHAT_ID` is always coerced to role=admin in `db._migrate()`
5. **Pending users blocked** — `access.gate` runs at `group=-1`; raises `ApplicationHandlerStop` to drop them
6. **Crisis route bypasses everything** — qa.py `_is_crisis()` runs before LLM/KB lookup
7. **Idempotent migrations** — `_migrate()` runs on every boot; new columns use `ALTER TABLE` with default that doesn't break old rows
8. **Admin cap** — `MAX_ADMINS` (default 2) enforced in `roles.set_role()` via `AdminCapReached`

## 8. First message for the next session

Paste this into a new Claude Code session to resume:

```
Resume Soul Coach bot project at /Users/dustinngo/Project/Bot_The_Soul_Coach
(v2.10.1). Read HANDOFF.md first, then AUDIT.md for what's next.
Today I want to: <fill in what you want to do>
```

If you want to continue from the roadmap, common next steps:
- "Implement /snooze command" (v2.12)
- "Add structured JSON logging" (v2.11)
- "Paginate /users command" (v2.13)
- "Async LLM calls" (v3.0 prep)
