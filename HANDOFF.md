# Soul Coach Bot — Session Handoff (v2.1)

> Copy-paste this into a new session to resume work with full context.

## What this is
A Python Telegram bot acting as a proactive mental coach. Pings users for scheduled task check-ins, answers questions from a curated Knowledge Base (KB), tracks satisfaction, escalates to a single Supervisor (S) when it can't help, and DMs S a weekly report. Workspace folder on disk: `Bot_The_Soul_Coach`.

## Functional requirements (locked)
- **Proactive reminders**: cron-scheduled per-user pings → DM with mood scale (😣😕😐🙂😄). 12h nudge, 24h mark missed. Restart-recovery for orphan check-ins.
- **Crisis pre-filter**: EN+VI keyword list checked before any LLM call → safe-messaging reply with hotlines. No escalation triggered for crisis messages.
- **KB Q&A**: free-text → fuzzy retrieval → if score ≥ threshold answer directly; else Gemini RAG soft-reply grounded in top-5 KB candidates.
- **Satisfaction**: hybrid — explicit 👍/👎 buttons + EN+VI text classifier. `sat_counter` per user. +1 on negative, reset on positive. Threshold 5.
- **Escalation triggers**: (a) KB miss + 👎 on LLM reply, (b) counter ≥ 5, (c) manual `/talk_to_human`. Notify single Supervisor with last 5 turns + inline "Mark resolved".
- **Weekly report**: Sun 18:00 cron. Per-user check-in compliance, mood trend, interactions, escalations, KB-promotion candidates. Snippets redacted; `/transcript` for verbatim (audit-logged).

## Decisions made (don't re-litigate)
1. KB in **SQLite `kb_entries` table**, S manages via `/kb_add /kb_list /kb_edit /kb_del`.
2. **Single supervisor** (`SUPERVISOR_CHAT_ID`).
3. Satisfaction = **hybrid** buttons + text inference (EN+VI).
4. **24h** miss window, **12h** nudge.
5. Mood scale on every check-in.
6. **LLM soft-reply BEFORE escalation** on KB miss. 👍 → log as `kb_candidate=1` so S can `/kb_promote <interaction_id>` to grow KB. This is the learning loop.
7. Privacy: report snippets redacted (60ch + sha1[:8]); `/transcript` for verbatim.
8. Hosting: **Oracle Cloud Always Free, Ampere A1 Flex (2 OCPU/12 GB)**. Stay on Always Free account; never click "Upgrade to Pay-As-You-Go".
9. `/pause` and `/resume` now actually call `scheduler().pause_job()` / `resume_job()` — not just a DB flag.
10. Health check on `GET /health` port 8080 (daemon thread, no extra dependency).
11. Timezone prompt on `/start` for new users. Reply validated with `pytz`; ignored if invalid.

## Tech stack
`python-telegram-bot` v21+ (async, long-polling) · `APScheduler` AsyncIOScheduler in-process · stdlib `sqlite3` WAL mode + threading.RLock · `rapidfuzz>=3.11` with **`token_set_ratio`** scorer (NOT WRatio) · `google-generativeai` Gemini Flash · `pytz` for tz validation · PyYAML for one-time KB seed · **Python 3.11–3.13** (3.14 breaks the rapidfuzz build).

## Architecture
```
main.py
  ├── db.init_db()                      # schema.sql + seed kb_seed.yaml once
  ├── start_health_server()             # daemon HTTP on HEALTH_PORT (8080)
  ├── ApplicationBuilder().post_init    # then…
  │       └── reminders.start_scheduler  # re-arm task jobs + weekly report
  └── handlers:
        Commands       → handlers/{onboarding,tasks,escalation,admin}.py
        CallbackQuery  → mood:* / sat:* / resolve:*
        TextMessage    → handlers/qa.py  (main Q&A flow)

services/
  kb.py           CRUD + cached fuzzy retrieval (token_set_ratio)
  llm.py          Gemini RAG: prompt = system + KB ctx + history + query
  satisfaction.py classify(text), counter ops, escalated state
  reminders.py    schedule_task_job, mood_callback, orphan recovery
  reports.py      weekly aggregate (markdown + JSON attachment)
  health.py       GET /health daemon thread (NEW in v2.1)
```

Message pipeline in `qa.on_user_message`:
1. `handle_tz_reply()` — consumed if user is in onboarding tz prompt
2. `_log_in()` — log the message
3. `_is_crisis()` — safe-messaging reply and return if crisis keyword matched
4. `is_escalated()` — silent if escalated
5. `classify()` — satisfaction signal from free text
6. `kb.search()` — fuzzy retrieval
7. Direct KB answer OR Gemini RAG

State machine per user: `IDLE → AWAITING_CHECKIN → IDLE` for reminders. `IDLE → IN_QA → {KB hit + 👍 IDLE / KB hit + 👎 c++ / KB miss → LLM → 👍 IDLE / 👎 ESCALATED / c==5 ESCALATED} → /resolve → IDLE c=0`.

## Data model (10 tables)
`users, tasks, check_ins, interactions, kb_entries, sessions, escalations, reports, audit_log` (see `schema.sql`). Key columns: `interactions.llm` (was reply LLM-generated), `interactions.satisfied` (NULL/0/1), `sessions.sat_counter`, `sessions.escalated_at`, `check_ins.mood` (1–5).

## Project layout
```
Bot_The_Soul_Coach/
├── SPEC.md README.md HANDOFF.md TESTPLAN.md
├── config.py db.py main.py schema.sql kb_seed.yaml
├── requirements.txt .env.example .gitignore
├── handlers/  onboarding.py tasks.py qa.py escalation.py admin.py
├── services/  kb.py llm.py satisfaction.py reminders.py reports.py health.py
├── utils/     timez.py
├── deploy/    ORACLE_DEPLOY.md soul-coach.service keepalive.{sh,service,timer}
│              backup_offhost.sh
├── .github/   workflows/ci.yml
└── tests/     test_smoke.py test_unit.py
```

## Env vars
**Required**: `TELEGRAM_TOKEN, SUPERVISOR_CHAT_ID, GEMINI_API_KEY`.
**Optional defaults**: `GEMINI_MODEL=gemini-2.0-flash, DB_PATH=data/soul_coach.db, DEFAULT_TZ=Asia/Ho_Chi_Minh, REMINDER_NUDGE_HOURS=12, REMINDER_MISS_HOURS=24, REPORT_CRON=0 18 * * SUN, FUZZY_THRESHOLD=70, SAT_THRESHOLD=5, LOG_LEVEL=INFO, HEALTH_PORT=8080`.

## Commands
- **User**: `/start /help /tasks /addtask /removetask /pause /resume /talk_to_human`
- **Supervisor**: `/users /report /resolve /transcript /kb_add /kb_list /kb_edit /kb_del /kb_promote`
- `/addtask` syntax: `/addtask <title> | <5-field cron>` e.g. `/addtask Morning meditation | 0 8 * * *`. Validated via `CronTrigger.from_crontab`.

## Oracle Always Free — two guarantees
1. **Never charged**: stay on Always Free account, no upgrade. Set $0.01 budget alert. Only Always-Free-eligible resources can be created on a non-upgraded account.
2. **Never reclaimed for being idle**: Oracle reclaims when 95p CPU < 20% over 7 days. `keepalive.timer` runs every 5 min and burns 60s of CPU = ~20% utilization rate. Also does SQLite WAL checkpoint so the work is useful. Log into console monthly (account idleness ≥ 30d can suspend).

## Important gotcha (real bug fixed during build)
**Do NOT use `rapidfuzz.WRatio` for KB retrieval.** It returns 85+ even for completely unrelated queries (e.g. "how do I solder a microcontroller pin" scored 86). We use **`token_set_ratio`**, which scores 90–100 for genuine matches and 30–50 for unrelated queries. Threshold of 70 with this scorer correctly routes obscure queries to the LLM fallback. Documented in `services/kb.py` docstring.

## Done (v2.1 — all original TODO items completed)
v2 spec locked · scaffold + requirements + README · DB + KB CRUD with cache invalidation · onboarding + task management + cron validation · APScheduler reminders (cron + 12h nudge + 24h miss + restart recovery + mood callback) · Q&A pipeline (KB → direct or Gemini RAG) · Gemini Flash client with grounded prompt + safety settings + crisis guardrails · EN/VI satisfaction classifier + counter + escalation state · 3 escalation paths with supervisor card + inline resolve · weekly report (markdown + JSON, redacted snippets, `/transcript`, `/kb_promote`) · Oracle deploy playbook + systemd unit + keepalive · smoke test passing.

**v2.1 additions (this session):**
- **Crisis filter**: `_CRISIS_KEYWORDS` list (EN+VI) in `handlers/qa.py`, checked before LLM
- **Pause fix**: `/pause` now calls `scheduler().pause_job()` per task; `/resume` calls `resume_job()`
- **Health endpoint**: `services/health.py` daemon HTTP thread; `HEALTH_PORT` env var
- **Timezone prompt**: `handlers/onboarding.handle_tz_reply()` + `_awaiting_tz` set; `pytz` validation
- **Unit tests**: `tests/test_unit.py` — 16 tests covering all v2.1 features, no credentials needed
- **CI**: `.github/workflows/ci.yml` — smoke + unit jobs on every push/PR
- **Off-host backups**: `deploy/backup_offhost.sh` — rclone to free remote, 14-day retention

## TODO (remaining)
- **Real run**: bot has never been pointed at a real Telegram token / Gemini key.
- **`/transcript` week parsing**: uses SQLite `strftime('%W', …)` — ISO-ish, may drift across years.
- **Embeddings retrieval (v2)**: drop-in for `KBRetriever` once KB > 50 entries.
- **Off-host rclone setup**: `backup_offhost.sh` is written; `rclone config` still needs to run on the VM (see ORACLE_DEPLOY.md §7).
- **UptimeRobot HTTP monitor**: wire up `http://<vm-ip>:8080/health` after deploy.

## Known design limits (intentional)
Single SQLite shared connection (fine for <100 users). APScheduler in-process — jobs re-armed from DB on boot so nothing is lost. Sentiment classifier is regex-based EN+VI, no negation handling — good enough for a satisfaction signal. Bot supports one Supervisor only. Timezone onboarding state (`_awaiting_tz`) is in-memory — lost on restart, which is fine (user can re-set tz anytime by contacting the supervisor or via a future `/settz` command).

## Quick re-start in a new session
```bash
cd Bot_The_Soul_Coach
python3.13 -m venv .venv && source .venv/bin/activate   # use 3.11–3.13; 3.14 breaks rapidfuzz
pip install -r requirements.txt
cp .env.example .env  # fill TELEGRAM_TOKEN, SUPERVISOR_CHAT_ID, GEMINI_API_KEY
python -m tests.test_smoke   # → ALL SMOKE TESTS PASSED
python -m tests.test_unit    # → Ran 16 tests ... OK
python main.py               # long-polls Telegram
```

Production: follow `deploy/ORACLE_DEPLOY.md` §1–§11.
