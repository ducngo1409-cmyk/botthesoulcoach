# Soul Coach — Telegram Bot (v2.10)

A Vietnamese-first mental-coach Telegram bot. Pings users for task check-ins, answers
from a curated Knowledge Base (KB), uses Gemini Flash as a grounded RAG fallback
on KB miss, escalates to a human Supervisor (S) when needed, and DMs S a weekly
report.

## Documentation

| Audience | Document |
|---|---|
| End user | [USER_GUIDE.md](USER_GUIDE.md) — Vietnamese, 5-min read |
| Supervisor / Admin | [ADMIN_GUIDE.md](ADMIN_GUIDE.md) — operation + escalation + KB review |
| Developer | [SPEC.md](SPEC.md) — full design |
| QA | [TESTPLAN.md](TESTPLAN.md) — test strategy |
| DevOps | [deploy/GCP_DEPLOY.md](deploy/GCP_DEPLOY.md) — step-by-step deploy |

## What's new in v2.10

**RBAC roles** — 4 explicit roles with permission matrix (pattern from Discord MEE6 / Telegram Combot):

| Role | Powers |
|---|---|
| **admin** 👑 | Full power: user lifecycle, role mgmt, broadcast, delete |
| **coacher** 🎓 | Handles users: KB, escalations, transcripts, settask, dm |
| **service** ⚙️ | Read-only (debug, view users) for monitoring/automation |
| **user** 👤 | Regular end user; own resources only |

- New commands: `/promote <id> <role>`, `/demote <id>`, `/roles`, `/myrole`
- `/help` is role-aware — shows only commands the caller can use
- Internal notifications (pending users, escalations, KB pending) fan out to ALL holders of the relevant permission instead of only `SUPERVISOR_CHAT_ID`
- `SUPERVISOR_CHAT_ID` auto-promoted to `admin` at boot (cannot be demoted)

See [ADMIN_GUIDE §0](ADMIN_GUIDE.md) for the full permission matrix.

## What's new in v2.9

Full user-management suite for supervisor:

- **View**: `/users [filter]` (filter by access or operational state), `/user <id>` (profile + stats), `/user_tasks <id>`
- **Access**: `/revoke <id>` (take back approved access)
- **Operational**: `/block`/`/unblock`, `/freeze`/`/unfreeze`
- **Communicate**: `/dm <id> <msg>`, `/broadcast <msg>` (to all approved+active)
- **Lifecycle**: `/reonboard <id>` (force tz re-prompt), `/delete_user <id> confirm` (hard delete + cascade)

See [ADMIN_GUIDE.md §2bis](ADMIN_GUIDE.md) for the full reference.

## What's new in v2.8

- **Request-to-join approval** — anyone can search and `/start` the bot, but they're locked in `pending` state until admin approves. Admin gets a DM with inline ✅ Duyệt / ❌ Từ chối buttons. Replaces the previous `ALLOWED_USER_IDS` env-based allowlist (removed).
- New supervisor commands: `/pending`, `/approve <user_id>`, `/reject <user_id>`. `/users` now shows status badges (✅⏳🚫).
- New env var `REQUIRE_APPROVAL=0` for dev/test instances that want fully open access.

## What's new in v2.7.2

- **Onboarding state persisted in DB** (`users.onboarded` column) so it survives bot restarts. Previous in-memory `_awaiting_tz` set was the root cause of users getting stuck mid-onboarding.
- **Strict state-machine isolation** — during onboarding, every input is routed correctly. Garbage text re-prompts, commands get a reminder, callbacks get a toast.

## What's new in v2.7.1

- (Superseded by v2.8) Allowlist gate via `ALLOWED_USER_IDS` env var.
- **Mandatory onboarding** — new users must finish setting timezone before using other commands; bot will gently remind them instead of letting commands silently no-op.
- **Onboarding skip bug fixed** — "không" / "khong" no longer accidentally triggers skip (was matching a very common Vietnamese word). Only explicit `skip`, `bỏ qua`, `/skip` keywords work now.
- New docs: [USER_GUIDE.md](USER_GUIDE.md), [ADMIN_GUIDE.md](ADMIN_GUIDE.md).

## What's new in v2.7

- **Friendly time format** in `/addtask` and `/settask`: `daily 22:30`, `weekdays 9:00`, `every 6 hours`, `every monday 8:00` — raw cron still works.
- **Timezone aliases** + `/tz` command: type `Hanoi`, `Tokyo`, `Vietnam`, `+7`, `UTC-5` instead of needing the exact IANA name.
- **Per-task pause/resume**: `/pause 3` or `/resume 3` controls a single reminder. No-arg form keeps the legacy "all" behavior.
- **Per-task nudge config**: `/nudge <task_id> <hours>` sets how long the bot waits before sending a follow-up nudge. `0` disables nudges for that task.
- **Improved welcome + help**: "Xin chào X, mình là Soul Coach của bạn" + concrete examples in every command description.

## What's new in v2.6

- **Pending-review KB queue** — 👍 on an LLM reply now creates a `pending` entry that's NOT used in search until S taps `✅ Approve` (or `/kb_approve <id>`). Protects KB quality from drift and duplicates.
- **Dedup gate** — auto-promote skipped if an active entry already covers the question (fuzzy ≥ 75).
- **Auto-keyword extraction** — VI+EN stopwords stripped, top 5 distinctive tokens kept.
- **Multi-model + multi-key LLM failover** — `GEMINI_MODEL` is comma-separated. On 429 / 5xx / empty / network error, bot tries every key on every model in order. Default chain: 4 models × 2 keys = 8 attempts.
- **Offline empathy fallback** — when all 8 attempts fail, user still gets a warm "kể thêm..." reply with `/talk_to_human` hint. Bot never goes silent.
- **Logrotate config** — weekly rotation, 4-week retention.
- New supervisor commands: `/kb_pending`, `/kb_approve`, `/kb_reject`.

## Quick start (local dev)

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # then fill in real values
python main.py             # initializes DB on first run
```

> **Python version:** use Python 3.11–3.13. 3.14 breaks the `rapidfuzz` build.

## Get the credentials

- `TELEGRAM_TOKEN` — talk to **@BotFather** on Telegram, run `/newbot`.
- `SUPERVISOR_CHAT_ID` — DM **@userinfobot**, copy your numeric `id`.
- `GEMINI_API_KEY` — https://aistudio.google.com/app/apikey (free tier).
- `GEMINI_API_KEY_2` (optional) — second key from a **different Google account** for genuine quota failover.

## Running tests

```bash
python -m tests.test_smoke && python -m tests.test_unit
```

Both run on every push via GitHub Actions (`.github/workflows/ci.yml`).

## Health endpoint

```bash
curl http://localhost:8080/health   # → ok
```

Use with UptimeRobot for free uptime alerting.

## Monitoring & debugging (production)

```bash
gcloud compute ssh soul-coach --zone=us-central1-a

# Live log stream
sudo tail -f /home/hallo_5ambloom/Bot_The_Soul_Coach/logs/bot.err.log

# Health-of-LLM filter (failover, quota, errors)
sudo tail -f /home/hallo_5ambloom/Bot_The_Soul_Coach/logs/bot.err.log \
  | grep -E 'tokens|429|5[0-9][0-9]|empty|escalat'
```

Healthy line: `LLM tokens [gemini-2.5-flash-lite key 0]: in=187 out=82 total=269`.
Auto-recovery from 503: a `LLM 503 …` warning followed immediately by a successful `tokens` line on a different model.

User Telegram commands:
- `/tz [city|offset]` — view / change timezone (e.g. `/tz Tokyo`, `/tz +7`)
- `/addtask <title> | <time>` — time is friendly OR cron
- `/tasks`, `/removetask <id>`, `/pause [id]`, `/resume [id]`, `/nudge <id> <hours>`
- `/talk_to_human` — connect to a human coach

Supervisor Telegram commands:
- **User mgmt (v2.9)**: `/users [filter]`, `/user <id>`, `/user_tasks <id>`, `/pending`, `/approve`/`/reject`/`/revoke`, `/block`/`/unblock`, `/freeze`/`/unfreeze`, `/dm <id> <msg>`, `/broadcast <msg>`, `/reonboard <id>`, `/delete_user <id> confirm`
- **KB mgmt**: `/kb_pending`, `/kb_approve <id>`, `/kb_reject <id>`, `/kb_add`, `/kb_list`, `/kb_edit`, `/kb_del`, `/kb_promote`
- **Ops**: `/debug` (live snapshot), `/report` (on-demand weekly), `/resolve <uid>` (close escalation), `/transcript <uid>`, `/settask <uid> | <title> | <time>` (assign with friendly time)

## Production deploy

| Platform | Guide | VM spec | Keepalive needed? |
|---|---|---|---|
| **GCP** (recommended) | [deploy/GCP_DEPLOY.md](deploy/GCP_DEPLOY.md) | e2-micro 1 GB RAM | ❌ No |
| Oracle Cloud | [deploy/ORACLE_DEPLOY.md](deploy/ORACLE_DEPLOY.md) | A1 Flex 12 GB RAM | ✅ Yes |

After deploying, install logrotate config:

```bash
sudo cp deploy/soul-coach.logrotate /etc/logrotate.d/soul-coach
sudo logrotate -d /etc/logrotate.d/soul-coach   # dry-run sanity check
```

## Layout

```
config.py        env loader
db.py            SQLite init + idempotent _migrate()
schema.sql       DDL (10 tables; kb_entries.status added in v2.6)
main.py          entry point

handlers/
  onboarding.py  /start, tz prompt, /tz, /help
  tasks.py       /addtask /removetask /pause [id] /resume [id] /nudge /tasks
  qa.py          crisis → KB → Gemini RAG → offline empathy
  escalation.py  /talk_to_human, resolve callback
  admin.py       /kb_* /report /users /transcript /settask /debug

services/
  kb.py          CRUD + status filter + dedup + keyword extraction
  llm.py         multi-model + multi-key failover, 429/5xx/empty
  timeparser.py  friendly time → cron (VI + EN)
  tz_aliases.py  city / country / offset → IANA
  satisfaction.py hybrid classifier + counter
  reminders.py   APScheduler + per-task nudge config + mood callback
  reports.py     weekly aggregate
  health.py      HTTP health daemon

deploy/          systemd, logrotate, keepalive, backups, GCP/Oracle guides
tests/           smoke + unit (no credentials)
.github/         CI workflow
```

## Token budget

Per LLM call (typical):
- system: ~60 tokens
- KB context: ≤ 100 (max 2 entries × 50, scored ≥ 40)
- history: ~60 (last 2 turns)
- query: ~30
- max output: 400

Total ~650 tokens. With 4 fallback models × 2 accounts × 1500 RPD free tier = ~12 000 calls/day theoretical capacity.
