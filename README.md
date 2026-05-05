# Soul Coach — Telegram Bot (v2.6)

A Vietnamese-first mental-coach Telegram bot. Pings users for task check-ins, answers
from a curated Knowledge Base (KB), uses Gemini Flash as a grounded RAG fallback
on KB miss, escalates to a human Supervisor (S) when needed, and DMs S a weekly
report.

See [SPEC.md](SPEC.md) for the full design and [TESTPLAN.md](TESTPLAN.md) for
the complete test strategy.

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

Supervisor Telegram commands:
- `/debug` — live snapshot (users, KB active+pending, escalations, recent LLM replies)
- `/kb_pending`, `/kb_approve <id>`, `/kb_reject <id>` — manage pending review queue
- `/report` — on-demand weekly report
- `/resolve <uid>` — close a stuck escalation
- `/settask <uid> | <title> | <cron>` — assign a reminder to a user

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
  onboarding.py  /start, tz prompt
  tasks.py       /addtask /removetask /pause /resume /tasks
  qa.py          crisis → KB → Gemini RAG → offline empathy
  escalation.py  /talk_to_human, resolve callback
  admin.py       /kb_* /report /users /transcript /settask /debug

services/
  kb.py          CRUD + status filter + dedup + keyword extraction
  llm.py         multi-model + multi-key failover, 429/5xx/empty
  satisfaction.py hybrid classifier + counter
  reminders.py   APScheduler + mood callback
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
