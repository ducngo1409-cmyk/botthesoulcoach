# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in TELEGRAM_TOKEN, SUPERVISOR_CHAT_ID, GEMINI_API_KEY
```

### Run
```bash
python main.py         # starts Telegram long-polling bot, initializes DB on first run
```

### Tests
```bash
python -m tests.test_smoke   # run from project root; no external API calls needed
```

## Architecture

This is a Telegram-based soul-coaching bot. The entry point is `main.py`, which initializes the SQLite database, seeds the knowledge base, registers all Telegram handlers, and starts the APScheduler.

### Handler → Service → DB flow

`handlers/` contains Telegram command and callback handlers. Each handler delegates domain logic to `services/`:

- `handlers/onboarding.py` — `/start`, `/help`, user registration
- `handlers/tasks.py` — `/tasks`, `/addtask`, `/removetask`, `/pause`, `/resume`; validates cron via `croniter`
- `handlers/qa.py` — free-text Q&A; tries KB fuzzy match first, falls back to Gemini RAG
- `handlers/escalation.py` — `/talk_to_human`, supervisor `/resolve`
- `handlers/admin.py` — supervisor commands: `/report`, `/users`, `/transcript`, KB CRUD (`/kb_add`, `/kb_edit`, etc.)

`services/` holds stateless domain logic:

- `services/kb.py` — fuzzy KB search (rapidfuzz, default threshold 70%), CRUD, seed loading from `kb_seed.yaml`
- `services/llm.py` — Gemini 2.0 Flash wrapper; builds RAG prompt with KB context when KB misses
- `services/reminders.py` — APScheduler job management for per-user check-ins, 12h nudges, 24h missed markers
- `services/satisfaction.py` — keyword-based mood classifier (EN+VI), increments/resets the per-user dissatisfaction counter
- `services/reports.py` — weekly supervisor summary generation

`db.py` manages SQLite connection pooling and transactions. Schema lives in `schema.sql` (9 tables: `users`, `tasks`, `check_ins`, `interactions`, `kb_entries`, `sessions`, `escalations`, `reports`, `audit_log`).

### Escalation state machine

Each user session tracks a dissatisfaction counter (`sessions.sat_counter`). The counter increments on a negative satisfaction signal (keyword classifier) after a KB hit, or on a Gemini RAG reply with negative feedback. When it reaches `SAT_THRESHOLD` (default 5), the user is escalated to the supervisor. Counter resets to 0 on a positive signal.

```
KB hit + positive → counter = 0
KB hit + negative → counter++
KB miss → Gemini RAG fallback
  positive → counter = 0
  negative → counter++ → if counter == SAT_THRESHOLD → ESCALATED
manual /talk_to_human → ESCALATED immediately
```

### Scheduling

APScheduler jobs are per-user and keyed on `user_id`. `services/reminders.py` adds/replaces/removes jobs when tasks are created, paused, or deleted. The weekly report job is a fixed cron from `REPORT_CRON` env var (default: `0 18 * * SUN`).

### Environment variables

Required: `TELEGRAM_TOKEN`, `SUPERVISOR_CHAT_ID`, `GEMINI_API_KEY`

Optional (defaults shown):
```
GEMINI_MODEL=gemini-2.0-flash
DB_PATH=data/soul_coach.db
DEFAULT_TZ=Asia/Ho_Chi_Minh
FUZZY_THRESHOLD=70
SAT_THRESHOLD=5
REMINDER_NUDGE_HOURS=12
REMINDER_MISS_HOURS=24
REPORT_CRON=0 18 * * SUN
LOG_LEVEL=INFO
```

### Deployment

Production runs on Oracle Always Free (Ubuntu). Systemd units are in `deploy/`:
- `soul-coach.service` — runs `main.py`
- `keepalive.timer` + `keepalive.sh` — pings the instance every 5 minutes to prevent Oracle's idle-reclamation from terminating it

Full deployment steps: `deploy/ORACLE_DEPLOY.md`.
