# Soul Coach — Telegram Bot (v2.1)

A proactive mental-coach Telegram bot. Pings users for task check-ins, answers
from a curated Knowledge Base (KB), uses Gemini Flash as a grounded RAG fallback
on KB miss, escalates to a human Supervisor (S) when needed, and DMs S a weekly
report.

See [SPEC.md](SPEC.md) for the full design and [TESTPLAN.md](TESTPLAN.md) for
the complete test strategy.

## Quick start (local dev)

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # then fill in real values
python main.py             # initializes DB on first run
```

> **Python version:** use Python 3.11–3.13. Python 3.14 breaks the
> `rapidfuzz` build. The requirements pin `rapidfuzz>=3.11` which supports
> 3.11–3.13.

## Get the credentials

- `TELEGRAM_TOKEN` — talk to **@BotFather** on Telegram, run `/newbot`.
- `SUPERVISOR_CHAT_ID` — DM **@userinfobot**, copy your numeric `id`.
- `GEMINI_API_KEY` — https://aistudio.google.com/app/apikey (free tier).

## Running tests

No credentials needed — tests use a temp DB and mock objects.

```bash
source .venv/bin/activate

# Smoke tests (imports, DB schema, KB retrieval, satisfaction classifier)
python -m tests.test_smoke

# Unit tests (crisis filter, health endpoint, tz prompt, reminders, pause/resume)
python -m tests.test_unit
```

Both suites also run automatically on every push via GitHub Actions
(`.github/workflows/ci.yml`).

## Health endpoint

The bot exposes `GET /health` on `HEALTH_PORT` (default 8080) from a daemon
thread. Use it with UptimeRobot (HTTP monitor) for free uptime alerting.

```bash
curl http://localhost:8080/health   # → ok
```

Remember to open port 8080 inbound in the Oracle VCN security list.

## Production deploy

Two Always Free options — pick one:

| Platform | Guide | VM spec | Keepalive needed? |
|---|---|---|---|
| **GCP** (recommended) | [deploy/GCP_DEPLOY.md](deploy/GCP_DEPLOY.md) | e2-micro 1 GB RAM | ❌ No |
| Oracle Cloud | [deploy/ORACLE_DEPLOY.md](deploy/ORACLE_DEPLOY.md) | A1 Flex 12 GB RAM | ✅ Yes |

GCP is simpler: no idle-reclamation, no keepalive script, and no Oracle account gotchas.

## Layout

```
config.py        env loader (includes HEALTH_PORT)
db.py            SQLite init from schema.sql, connection helper
schema.sql       DDL (10 tables)
main.py          entry point: DB init, health server, handlers, scheduler

handlers/
  onboarding.py  /start with timezone prompt, handle_tz_reply
  tasks.py       /addtask /removetask /pause /resume (pause actually suspends jobs)
  qa.py          free-text Q&A: crisis filter → KB → Gemini RAG
  escalation.py  /talk_to_human, resolve callback
  admin.py       supervisor commands (/kb_* /report /users /transcript)

services/
  kb.py          CRUD + cached fuzzy retrieval (token_set_ratio)
  llm.py         Gemini RAG grounded prompt
  satisfaction.py hybrid classifier + counter + escalation state
  reminders.py   APScheduler cron jobs + mood callback
  reports.py     weekly aggregate (markdown + JSON)
  health.py      tiny HTTP health-check server (daemon thread)

utils/           helpers (timezone)
deploy/          systemd units, keepalive, off-host backup, deploy playbook
tests/
  test_smoke.py  smoke tests (no credentials)
  test_unit.py   unit tests (no credentials)
.github/
  workflows/ci.yml  CI: smoke + unit on every push
```
