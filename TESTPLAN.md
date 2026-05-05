# Soul Coach Bot — Test Plan (v2.1)

Two automated test suites cover all logic that doesn't require live credentials.
Manual integration tests are listed for first-run verification before going to production.

---

## 1. Automated tests (no credentials needed)

### 1.1 Smoke tests — `tests/test_smoke.py`

Verifies the project can be imported, DB schema is correct, and core services
behave as expected.  Requires only the project venv — no Telegram token or
Gemini API key.

**What it covers:**

| # | Check |
|---|---|
| 1 | All modules import without error |
| 2 | `db.init_db()` creates 10 tables |
| 3 | KB seed loads correctly (≥ 8 entries) |
| 4 | KB search — positive hit: `"I can't focus today"` → `focus` entry, score ≥ 70 |
| 5 | KB search — three obscure off-topic queries each score < 70 (miss → LLM path) |
| 6 | `satisfaction.classify` — 5 cases EN+VI (positive / negative / neutral) |
| 7 | Satisfaction counter increment / reset cycle |
| 8 | KB CRUD round-trip: add → get → edit → delete |
| 9 | Cron expression validation (valid + invalid inputs) |

**Run:**

```bash
cd Bot_The_Soul_Coach
source .venv/bin/activate
python -m tests.test_smoke
```

Expected output ends with: `✅ ALL SMOKE TESTS PASSED`

---

### 1.2 Unit tests — `tests/test_unit.py`

Tests the five features added in v2.1 using mocked Telegram objects and a
temp SQLite DB. No live credentials required.

**What it covers:**

| Class | Tests |
|---|---|
| `TestCrisisFilter` | `_is_crisis()` matches EN+VI keywords; non-crisis text returns False |
| `TestCrisisHandler` | Crisis message → safe-messaging reply, `soft_reply` NOT called; non-crisis → KB called |
| `TestHealthEndpoint` | `GET /health` → HTTP 200 + body `ok`; unknown path → 404 |
| `TestTimezonePrompt` | New user gets tz prompt + `_awaiting_tz` set; returning user gets no prompt |
| `TestTimezonePrompt` | Valid tz reply updates `users.tz` in DB and clears flag |
| `TestTimezonePrompt` | Invalid tz reply clears flag and sends warning message |
| `TestReminderCore` | `_mark_missed` sets status=`missed`; does NOT overwrite status=`answered` |
| `TestReminderCore` | `_send_checkin` skips users with status=`paused` (no bot.send_message call) |
| `TestReminderCore` | `_send_checkin` creates a `check_ins` row for active users |
| `TestPauseResume` | `/pause` sets `users.status='paused'` AND calls `scheduler().pause_job()` |
| `TestPauseResume` | `/resume` sets `users.status='active'` AND calls `scheduler().resume_job()` |

**Run:**

```bash
cd Bot_The_Soul_Coach
source .venv/bin/activate
python -m tests.test_unit
```

Expected output ends with: `OK` and `Ran 16 tests`.

---

### 1.3 Run both suites together

```bash
cd Bot_The_Soul_Coach
source .venv/bin/activate
python -m tests.test_smoke && python -m tests.test_unit
```

Both must pass before any push to `main`.

---

### 1.4 CI (GitHub Actions)

`.github/workflows/ci.yml` runs both suites on every push and pull request
using Python 3.11 (stable, fully supported by all dependencies).

The workflow has two parallel jobs:

| Job | Command |
|---|---|
| `smoke` | `python -m tests.test_smoke` |
| `unit` | `python -m tests.test_unit` |

Status badges appear on the GitHub repo page once the repo is pushed.

---

## 2. Manual integration tests (requires live credentials)

These cannot be automated without real Telegram + Gemini keys.
Run them once before declaring the bot production-ready.

### Setup

```bash
cp .env.example .env
# Fill in: TELEGRAM_TOKEN, SUPERVISOR_CHAT_ID, GEMINI_API_KEY
source .venv/bin/activate
python main.py
```

### Checklist

#### 2.1 Bot startup
- [ ] Bot starts without error; `logs/` directory created
- [ ] `GET http://localhost:8080/health` returns `200 ok`
- [ ] DB created at `data/soul_coach.db`

#### 2.2 Onboarding
- [ ] `/start` → welcome message + timezone prompt
- [ ] Reply with `Asia/Tokyo` → confirmation `Timezone set to Asia/Tokyo`
- [ ] `/start` again (returning user) → single welcome-back message, no tz prompt

#### 2.3 KB Q&A
- [ ] Type: `I can't focus today` → KB direct answer + 👍/👎
- [ ] Press 👍 → `🌟 Glad that helped.`
- [ ] Type obscure question → LLM soft reply with `💡` prefix + 👍/👎
- [ ] Press 👎 on LLM reply → escalation message sent to supervisor

#### 2.4 Crisis filter
- [ ] Type: `I've been thinking about suicide` → crisis reply with hotline number, NO LLM called
- [ ] Confirm supervisor receives NO escalation for crisis message (bot handles it directly)

#### 2.5 Reminders
- [ ] `/addtask Test reminder | * * * * *` (every minute) → confirmation
- [ ] Wait up to 90s → check-in DM arrives with mood keyboard
- [ ] Tap a mood emoji → `Mood logged: 🙂`
- [ ] `/pause` → 🔕 message; wait another minute → no new check-in arrives
- [ ] `/resume` → 🔔 message; wait another minute → check-in arrives again
- [ ] `/removetask <id>` → task removed

#### 2.6 Escalation flow
- [ ] `/talk_to_human` → escalation card sent to supervisor with last 5 turns + "Mark resolved" button
- [ ] Supervisor taps "Mark resolved" → user receives resolution message

#### 2.7 Supervisor KB management
- [ ] `/kb_add test | What is X? | X is a test. | x,test`
- [ ] `/kb_list test` → shows new entry
- [ ] `/kb_edit <id> answer=Updated answer.`
- [ ] `/kb_del <id>`

#### 2.8 Weekly report (on demand)
- [ ] Supervisor `/report` → markdown report DM + JSON file attachment

#### 2.9 Health endpoint (UptimeRobot)
- [ ] Navigate to `http://<server-ip>:8080/health` → `ok`
- [ ] Open port 8080 in Oracle VCN security list (ingress rule)
- [ ] Add UptimeRobot HTTP monitor pointing at `http://<server-ip>:8080/health`

#### 2.10 Off-host backup (post-deploy)
- [ ] `rclone config` — add remote named `backup`
- [ ] `chmod +x deploy/backup_offhost.sh && deploy/backup_offhost.sh`
- [ ] Verify snapshot appears in `~/backups/` and on the rclone remote
- [ ] `crontab -e` — add entry per `deploy/backup_offhost.sh` header comment

---

## 3. Regression checklist after any future change

Before merging a PR:

```
[ ] python -m tests.test_smoke  — passes
[ ] python -m tests.test_unit   — passes
[ ] No new imports of rapidfuzz.WRatio (use token_set_ratio only)
[ ] No raw os.environ["TELEGRAM_TOKEN"] — go through config.settings()
[ ] crisis filter keywords still in handlers/qa.py _CRISIS_KEYWORDS list
```
