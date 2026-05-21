# Soul Coach Telegram Bot — Specification (v2.9)

> Source of truth for implementation. Updated as features land.
>
> **v2.9** (current): Full user-management suite for supervisor — `/users [filter]`, `/user <id>`, `/user_tasks`, `/revoke`, `/block`/`/unblock`, `/freeze`/`/unfreeze`, `/dm`, `/broadcast`, `/reonboard`, `/delete_user`.
> v2.8: Request-to-join approval model — anyone can /start, admin gets DM with Approve/Reject buttons, user locked until approved. Replaces env-based ALLOWED_USER_IDS allowlist.
> v2.7.2: DB-backed onboarding state survives restarts; strict state-machine isolation in access.gate.
> v2.7.1: Mandatory onboarding enforcement, fix "không" skip-keyword regression, USER_GUIDE + ADMIN_GUIDE.
> v2.7: Friendly time parser, timezone aliases + `/tz` command, per-task pause/resume by id, per-task nudge config (`/nudge`), improved welcome and help.
> v2.6: Pending-review KB queue, dedup gate, auto-keyword extraction, multi-model + multi-key LLM failover with 5xx handling, offline empathy fallback, logrotate.
> v2.5: Vietnamese KB + UI, /debug, /settask, escalation auto-clear, token optimization (~75% reduction), multi-key failover, typing indicator.

---

## 1. Purpose & Scope

A proactive Telegram bot that acts as a mental coach: pings users for scheduled task check-ins, answers from a curated Knowledge Base (KB), tracks satisfaction, escalates to a human Supervisor (S) when it can't help, and DMs S a weekly aggregate report.

**Out of scope (v1):** real therapy, free-form unbounded LLM, multi-supervisor, voice/video.

## 2. Actors

| Actor | Identity |
|---|---|
| User (U) | Telegram `user_id` |
| Supervisor (S) | Single fixed `SUPERVISOR_CHAT_ID` |
| Bot (B) | Python service |

## 3. Tech Stack

- `python-telegram-bot` v21+ (async); `ChatAction` is in `telegram.constants`
- `SQLite` via stdlib `sqlite3` (WAL mode, single shared connection)
- `APScheduler` AsyncIOScheduler for reminders + weekly report
- `rapidfuzz` ≥ 3.11 for KB fuzzy matching (`token_set_ratio` scorer — see §12)
- `google-genai` (Gemini Flash) for grounded RAG fallback; `system_instruction` via `GenerateContentConfig`
- `pytz` for timezone validation

## 4. Data Model

```sql
users(tg_id PK, name, tz, joined_at, status)              -- active|paused|blocked
tasks(id PK, user_id FK, title, cron_expr, active,
      nudge_hours NULL, max_nudges)                       -- per-task nudge config
check_ins(id PK, task_id FK, user_id FK, sent_at, replied_at,
          reply_text, mood INT, status)                   -- pending|answered|missed
interactions(id PK, user_id, ts, direction, text, intent,
             kb_match_id NULL, llm BOOLEAN, satisfied)    -- direction: in|out
kb_entries(id PK, category, question, answer, keywords,
           created_by, created_at, hits, status)          -- status: active|pending
sessions(user_id PK, sat_counter, last_unsat_at,
         current_topic, escalated_at NULL)
escalations(id PK, user_id, reason, context_json,
            sent_to_s_at, resolved_at NULL)               -- reason: kb_miss|counter|manual
reports(id PK, week_start, week_end, payload_json, sent_at)
audit_log(id PK, ts, actor, action, target)
```

**Migrations**: `db._migrate()` runs idempotently on boot.
- v2.6: adds `kb_entries.status`
- v2.7: adds `tasks.nudge_hours` (NULL = use global default) and `tasks.max_nudges` (0 = no follow-up nudges)

## 5. State Machine (per user)

```
IDLE
 ├─ reminder fired ──▶ AWAITING_CHECKIN
 │     ├─ reply within 12h ──▶ IDLE
 │     ├─ 12h nudge sent ─────▶ AWAITING_CHECKIN
 │     └─ 24h no reply ───────▶ MISSED ──▶ IDLE
 │
 └─ user msg ──▶
       ├─ crisis keywords ──▶ safe-messaging reply (no escalation, no log_out)
       ├─ ESCALATED ────────▶ wait-reminder (not silent)
       ├─ tz onboarding ────▶ sets users.tz, IDLE
       └─ IN_QA
             ├─ KB hit (score ≥ 70 on ACTIVE entries) ──▶ direct answer + 👍/👎
             │     ├─ 👍 → IDLE (counter=0)
             │     └─ 👎 → counter++
             └─ KB miss ──▶ Gemini RAG (model+key failover, offline fallback)
                   ├─ 👍 → IDLE + auto-promote to KB as PENDING + DM S with Approve/Reject buttons
                   └─ 👎 → counter++
       counter ≥ 10 ──▶ ESCALATED

ESCALATED ── /resolve OR auto-clear after 24h ──▶ IDLE (counter=0)
```

## 6. Functional Modules

### 6.0 Access Gate (v2.8)
`handlers/access.gate` is a `TypeHandler` installed at `group=-1` (runs before any feature handler). Raises `ApplicationHandlerStop` to drop unauthorized updates entirely.

**Two gates in order:**

1. **Approval gate** — checks `users.access_status` (DB column):
   - `approved` → fall through to gate 2
   - `pending` → rate-limited reply ("đang chờ duyệt", once per 30s/user), drop
   - `rejected` → silent drop
   - missing row (no /start yet) → only `/start` proceeds; everything else gets "Gõ /start để bắt đầu"
   - Supervisor is always allowed and bypasses both gates entirely.

   New users land in `pending` after `/start`; supervisor receives a DM with **✅ Duyệt** / **❌ Từ chối** inline buttons (callback prefix `usr_app:` / `usr_rej:`).

   Toggle via `REQUIRE_APPROVAL` env var (default `1`). Set to `0` to auto-approve everyone.

2. **Onboarding gate** — `users.onboarded` (DB column):
   - `1` → full access
   - `0` → only `/start`, `/tz <arg>`, `/talk_to_human`, and plain text (consumed as tz reply right at the gate) proceed. Other commands trigger a "finish tz first" reminder. Callback queries get an alert toast and are dropped.

State is fully DB-backed so it survives bot restarts.

### 6.1 Onboarding & Timezone
`/start` registers user → prompts for timezone with concrete examples ("Hanoi, Tokyo, +7").
- `services.tz_aliases.resolve_tz()` accepts:
  - Exact IANA names (`Asia/Ho_Chi_Minh`)
  - City/country aliases — case-insensitive, diacritic-insensitive (`Hà Nội`, `vietnam`, `vn`, `HCM`, `saigon`, `Tokyo`, `Singapore`, `London`, `NYC`, …)
  - UTC offsets (`+7`, `UTC-5`, `GMT+9`) → mapped to `Etc/GMT-N` (POSIX-flipped sign)
- Unknown input: bot replies with retry hints and keeps user in awaiting state
- `skip` (or `bỏ qua`) → keep default
- **`/tz [arg]`** can be invoked any time: no arg shows current; arg resolves and updates

### 6.2 Proactive Reminders
APScheduler fires per active task → mood-scale inline keyboard (😣😕😐🙂😄). Follow-ups respect per-task config from `tasks` table:
- `nudge_hours` (NULL = global `REMINDER_NUDGE_HOURS`, 0 = no nudge)
- `max_nudges` (0 = no follow-up; 1 = single nudge — current default)

`/pause` and `/resume` accept an optional `<task_id>` arg:
- `/pause` (no arg) — flip `users.status='paused'` + pause every active job for the user
- `/pause <id>` — flip `tasks.active=0` for that one task + pause its job only
- `/resume` / `/resume <id>` — symmetric
- `_send_checkin` skips if either `users.status != 'active'` OR `tasks.active = 0`

### 6.3 Crisis Pre-filter
EN+VI keyword substring match before any LLM call. On match → safe-messaging reply with hotline; no escalation, no `log_out` row (so it doesn't pollute interaction history).

### 6.4 KB Retrieval
- `kb.search()` filters `status = 'active'` → pending entries don't leak into matches.
- `rapidfuzz.token_set_ratio` over `question + keywords`.
- `top1.score ≥ FUZZY_THRESHOLD (65)` → direct answer + 👍/👎 + hit counter increment.

### 6.5 Satisfaction Counter (hybrid)
- Inline 👍/👎 buttons on every bot reply.
- Free-text classified by `services.satisfaction.classify()` (EN+VI regex with word boundaries).
- `+1` on negative; reset on positive; threshold = `SAT_THRESHOLD (10)`.
- Reset on positive feedback, escalation, `/resolve`, or 24h idle.

### 6.6 Gemini RAG Fallback
On KB miss:
1. Show typing indicator (`ChatAction.TYPING`).
2. Build minimal prompt: system_instruction (≈60 tokens, in `GenerateContentConfig`), top KB entries with score ≥ 40 (max 2, answer truncated to 100 chars), last 2 turns.
3. **Failover chain**: for each model in `GEMINI_MODEL` (comma-separated list), for each key in `GEMINI_API_KEY` / `GEMINI_API_KEY_2` — try; on 429 / 5xx / empty / network error → continue. Default order: `gemini-2.5-flash-lite, gemini-2.5-flash, gemini-2.0-flash-lite, gemini-2.0-flash` = 8 attempts.
4. **Offline empathy fallback**: when all attempts fail → bot still replies with a generic empathy template + `/talk_to_human` hint. Never silent.
5. Log `usage_metadata` (input/output token count) per call.
6. On 429 across the chain → DM S (rate-limited 1/10min); user still gets empathy template.

`max_output_tokens=400`. No `parse_mode` for LLM text (avoids markdown parse errors on user-supplied content).

### 6.7 Auto-KB Promotion (Pending Review Queue)
When a user 👍s an LLM reply:
1. **Dedup gate**: `kb.has_similar(question, threshold=75)` — if an active entry already covers it, skip silently.
2. **Length gate**: skip if question < 4 chars.
3. **Auto-extract keywords**: `kb.extract_keywords()` strips stopwords (VI+EN), keeps top 5 distinctive tokens.
4. Insert with `status='pending'`, `category='general'`. **Pending entries are excluded from `kb.search()` until approved.**
5. DM S with question + answer + extracted keywords + inline `✅ Approve` / `❌ Reject` buttons (callbacks: `kb_app:<id>` / `kb_rej:<id>`).
6. S can also use `/kb_pending`, `/kb_approve <id> [category] [keywords]`, `/kb_reject <id>` from the chat.

This protects KB quality from drift and duplicates while still letting the bot learn over time.

### 6.8 Escalation
Three triggers (`kb_miss`, `counter`, `manual`) → DM S with last 5 turns + Resolve button. Re-escalation suppressed if already escalated; user gets wait-reminder instead.

### 6.9 Weekly Report
Cron Sunday 18:00 (S timezone). Aggregates the past 7 days per user (compliance, mood trend, escalations) and globally (top KB hits, top KB misses, **pending KB count**). Markdown table + JSON attachment. Snippets redacted by default; verbatim view via `/transcript` (audit-logged).

### 6.10 Friendly Time Parser (`services/timeparser.py`)

Translates natural-language schedules into 5-field cron. Used by `/addtask` and `/settask`. Accepts EN+VI:

| Input | Output cron | Summary shown to user |
|---|---|---|
| `0 8 * * *` | passthrough | `mỗi ngày lúc 08:00` |
| `daily 22:30` | `30 22 * * *` | `mỗi ngày lúc 22:30` |
| `weekdays 9:00` | `0 9 * * 1-5` | `thứ 2 đến thứ 6 lúc 09:00` |
| `weekends 10:00` | `0 10 * * 0,6` | `cuối tuần lúc 10:00` |
| `every monday 8:00` | `0 8 * * 1` | `thứ 2 lúc 08:00` |
| `t2 t4 t6 7:00` | `0 7 * * 1,3,5` | `thứ 2, thứ 4, thứ 6 lúc 07:00` |
| `every 6 hours` | `0 */6 * * *` | `mỗi 6 giờ` |
| `every 30 minutes` | `*/30 * * * *` | `mỗi 30 phút` |

Failure returns a friendly help text with worked examples — used as the `/addtask` error message.

### 6.11 Health & Monitoring
`/health` HTTP endpoint on `HEALTH_PORT` (default 8080) for UptimeRobot. `/debug` supervisor command shows live snapshot: users, active+pending KB counts, open escalations, recent LLM replies.

## 7. Commands

> v2.7 changes: `/tz`, `/nudge`, per-task `/pause`/`/resume <id>`, friendly time in `/addtask` + `/settask`.

| Command | Who | Purpose |
|---|---|---|
| `/start` | U | Register, onboarding + timezone prompt |
| `/help` | U | Usage |
| `/tasks` | U | List my reminders |
| `/tz [city\|country\|offset]` | U | View or change timezone (e.g. `/tz Tokyo`, `/tz +7`) |
| `/addtask <title> \| <time>` | U | Add reminder. Time = friendly (`daily 22:30`, `weekdays 9:00`, `every 6 hours`) OR 5-field cron |
| `/removetask <id>` | U | Remove |
| `/pause [task_id]` | U | Pause one task (with id) or all (no id) |
| `/resume [task_id]` | U | Resume one task or all |
| `/nudge <task_id> <hours>` | U | Set per-task nudge interval. `0` disables follow-up nudges |
| `/talk_to_human` | U | Manual escalation |
| `/report` | S | On-demand weekly report |
| `/resolve <user_id>` | S | Close escalation |
| `/transcript <user_id> [YYYY-WW]` | S | View verbatim history |
| `/users [filter]` | S | List users; filter ∈ {pending, approved, rejected, active, paused, blocked} |
| `/user <user_id>` | S | Detailed profile + stats |
| `/user_tasks <user_id>` | S | List reminders for a user |
| `/pending` | S | List users awaiting access approval |
| `/approve <user_id>` | S | Grant access to a pending user |
| `/reject <user_id>` | S | Deny access (for pending users) |
| `/revoke <user_id>` | S | Take back an approved user's access |
| `/block <user_id>` | S | Set status=blocked (bot stops sending) |
| `/unblock <user_id>` | S | Reverse block |
| `/freeze <user_id>` | S | Pause all reminders for a user |
| `/unfreeze <user_id>` | S | Resume |
| `/dm <user_id> <msg>` | S | DM a specific user via the bot |
| `/broadcast <msg>` | S | DM all approved+active users (skipping S) |
| `/reonboard <user_id>` | S | Force the user to re-do tz onboarding |
| `/delete_user <user_id> confirm` | S | Hard delete user + all related rows (CASCADE) |
| `/settask <user_id> \| <title> \| <cron>` | S | Assign reminder to a user |
| `/kb_add <cat> \| <q> \| <a> \| <kw>` | S | Add active KB entry |
| `/kb_list [cat]` | S | Browse |
| `/kb_edit <id> <field>=<value>` | S | Update entry |
| `/kb_del <id>` | S | Delete entry |
| `/kb_pending` | S | List entries awaiting review |
| `/kb_approve <id> [category] [keywords]` | S | Promote pending → active |
| `/kb_reject <id>` | S | Delete pending entry |
| `/kb_promote <interaction_id>` | S | Manually promote a past LLM reply |
| `/debug` | S | Live status snapshot |

## 8. Configuration (env vars)

| Var | Default | Notes |
|---|---|---|
| `TELEGRAM_TOKEN` | — | required |
| `SUPERVISOR_CHAT_ID` | — | required |
| `GEMINI_API_KEY` | — | required |
| `GEMINI_API_KEY_2` | empty | optional 2nd-account failover |
| `GEMINI_MODEL` | `gemini-2.5-flash-lite,gemini-2.5-flash,gemini-2.0-flash-lite,gemini-2.0-flash` | comma-separated failover chain |
| `DB_PATH` | `data/soul_coach.db` | |
| `DEFAULT_TZ` | `Asia/Ho_Chi_Minh` | |
| `REMINDER_NUDGE_HOURS` | `12` | |
| `REMINDER_MISS_HOURS` | `24` | |
| `REPORT_CRON` | `0 18 * * SUN` | S timezone |
| `FUZZY_THRESHOLD` | `65` | rapidfuzz 0–100 |
| `SAT_THRESHOLD` | `10` | LLM tries before escalating |
| `LOG_LEVEL` | `INFO` | |
| `HEALTH_PORT` | `8080` | |
| `REQUIRE_APPROVAL` | `1` | `0` to auto-approve everyone (dev/test only) |
| `REQUIRE_ONBOARDING` | `1` | `0` to allow new users to use commands before setting tz |

## 9. Reliability & Continuous Operation

**Failure modes covered:**

| Failure | Behavior |
|---|---|
| Single key 429 quota | Failover to next key |
| All keys 429 on one model | Failover to next model in `GEMINI_MODEL` |
| Model 5xx (server overload) | Failover to next key/model |
| Empty response (safety filter) | Failover to next key/model |
| Network timeout | Failover to next key/model |
| All 8 attempts fail | Offline empathy template + `/talk_to_human` hint |
| Telegram 429 | PTB built-in exponential backoff |
| User blocks bot | `Forbidden` caught → `users.status='blocked'` |
| Stale escalation > 24h | Auto-cleared on boot via `db._clear_stale_escalations()` |
| Pending check-ins on restart | Marked `missed` if past window |
| Crisis keywords | Handled before LLM; no escalation, no log_out |
| Disk-full / log growth | Logrotate weekly, keep 4 (see `deploy/soul-coach.logrotate`) |

**Token budget per LLM call** (target ~650 total):
- system: ~60 (cached if eligible)
- KB context: ≤ 2 entries × 50 = 100
- history: 2 turns × 30 = 60
- user query: ~30
- max output: 400

At `gemini-2.5-flash-lite` free tier (1500 RPD/project): ~975K tokens/day per model per account. With 4 models × 2 accounts = ~12 000 calls/day theoretical capacity.

**Memory bounds**: KB entries cached in-process (single shared list). At 1000 active entries ≈ 500 KB heap; fuzzy search O(N) ≈ 5 ms. Pending entries excluded from cache match step.

## 10. Deployment (GCP e2-micro Always Free)

- Instance: `soul-coach`, zone `us-central1-a`, user `hallo_5ambloom`.
- Service: systemd `soul-coach.service`. Logs in `/home/hallo_5ambloom/Bot_The_Soul_Coach/logs/bot.err.log`.
- Logrotate: install `deploy/soul-coach.logrotate` to `/etc/logrotate.d/soul-coach` for weekly rotation, 4-week retention.
- Health: UptimeRobot pings `:8080/health`.
- Backups: `deploy/backup_offhost.sh` via rclone (cron nightly).
- Monitoring tail: `sudo tail -f logs/bot.err.log | grep -E 'tokens|429|error'`.

See `deploy/GCP_DEPLOY.md` for step-by-step.

## 11. Project Layout

```
Bot_The_Soul_Coach/
├── SPEC.md TESTPLAN.md README.md HANDOFF.md
├── requirements.txt .env.example .gitignore
├── config.py main.py schema.sql kb_seed.yaml db.py
├── handlers/
│   ├── onboarding.py    /start, tz prompt, /help
│   ├── tasks.py         /addtask /removetask /pause /resume /tasks
│   ├── qa.py            crisis filter, KB→LLM pipeline, auto-pending KB
│   ├── escalation.py    /talk_to_human, /resolve, callbacks
│   └── admin.py         /report /users /transcript /kb_* /settask /debug
├── services/
│   ├── kb.py            CRUD + status filter + dedup + keyword extraction
│   ├── llm.py           multi-model + multi-key failover
│   ├── satisfaction.py
│   ├── reminders.py
│   ├── reports.py
│   └── health.py
├── deploy/
│   ├── GCP_DEPLOY.md ORACLE_DEPLOY.md
│   ├── soul-coach.service soul-coach-gcp.service
│   ├── soul-coach.logrotate           ← weekly rotation
│   ├── keepalive.{service,timer,sh}
│   ├── backup_offhost.sh
│   └── migrate_vi_qa.py migrate_vi_keywords.py
├── .github/workflows/ci.yml
└── tests/test_smoke.py test_unit.py
```

## 12. Known Scorer Gotcha

**Do NOT use `rapidfuzz.WRatio`.** Returns 85+ for unrelated queries. Use `token_set_ratio` (90–100 for genuine matches, 30–50 for unrelated). Threshold 65 with Vietnamese KB. Enforced in `services/kb.py`.
