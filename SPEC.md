# Soul Coach Telegram Bot — Specification (v2.6)

> Source of truth for implementation. Updated as features land.
>
> **v2.6** (current): Pending-review KB queue, dedup gate, auto-keyword extraction, multi-model + multi-key LLM failover with 5xx handling, offline empathy fallback, logrotate.
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
tasks(id PK, user_id FK, title, cron_expr, active)
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

**Migrations**: `db._migrate()` runs idempotently on boot. Currently adds `kb_entries.status` column for existing DBs.

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

### 6.1 Onboarding
`/start` registers user → prompts for timezone (validated with `pytz`). Invalid/missing reply keeps `DEFAULT_TZ`.

### 6.2 Proactive Reminders
APScheduler fires per active task → mood-scale inline keyboard (😣😕😐🙂😄). 12h nudge → 24h missed. `/pause` and `/resume` actually suspend/resume scheduler jobs (not just status flip).

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

### 6.10 Health & Monitoring
`/health` HTTP endpoint on `HEALTH_PORT` (default 8080) for UptimeRobot. `/debug` supervisor command shows live snapshot: users, active+pending KB counts, open escalations, recent LLM replies.

## 7. Commands

| Command | Who | Purpose |
|---|---|---|
| `/start` | U | Register, onboarding + timezone prompt |
| `/help` | U | Usage |
| `/tasks` | U | List my reminders |
| `/addtask <title> \| <cron>` | U | Add reminder |
| `/removetask <id>` | U | Remove |
| `/pause`, `/resume` | U | Suspend/resume scheduler jobs |
| `/talk_to_human` | U | Manual escalation |
| `/report` | S | On-demand weekly report |
| `/resolve <user_id>` | S | Close escalation |
| `/transcript <user_id> [YYYY-WW]` | S | View verbatim history |
| `/users` | S | Active user list |
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
