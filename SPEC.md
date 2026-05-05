# Soul Coach Telegram Bot — Specification (v2.3)

> Final, locked-in spec after design discussion. Source of truth for implementation.
> v2.1 adds: crisis filter, real pause/resume, /health endpoint, tz onboarding,
> unit tests, CI, off-host backups.

---

## 1. Purpose & Scope

A proactive Telegram bot that acts as a mental coach: pings users for scheduled task check-ins, answers questions from a curated Knowledge Base (KB), tracks satisfaction, escalates to a human Supervisor (S) when it can't help, and DMs S a weekly aggregate report.

**Out of scope (v1):** real therapy, free-form unbounded LLM, multi-supervisor, voice/video, multi-language UI (EN+VI text-inference is in scope).

## 2. Actors

| Actor | Identity |
|---|---|
| User (U) | Telegram `user_id` |
| Supervisor (S) | Single fixed `SUPERVISOR_CHAT_ID` |
| Bot (B) | Python service |

## 3. Tech Stack

- `python-telegram-bot` v21+ (async)
- `SQLite` via stdlib `sqlite3`
- `APScheduler` (AsyncIOScheduler) for reminders + weekly report
- `rapidfuzz` ≥ 3.11 for KB fuzzy matching (`token_set_ratio` scorer — see §11)
- `google-generativeai` (Gemini Flash) for grounded RAG fallback
- `pytz` for timezone validation
- KB stored in SQLite `kb_entries` table; managed by S via admin commands

## 4. Data Model

```sql
users(tg_id PK, name, tz, joined_at, status)              -- active|paused|blocked
tasks(id PK, user_id FK, title, cron_expr, active)
check_ins(id PK, task_id FK, user_id FK, sent_at, replied_at,
          reply_text, mood INT, status)                   -- pending|answered|missed
interactions(id PK, user_id, ts, direction, text, intent,
             kb_match_id NULL, llm BOOLEAN, satisfied)    -- direction: in|out
kb_entries(id PK, category, question, answer, keywords,
           created_by, created_at, hits)
sessions(user_id PK, sat_counter, last_unsat_at,
         current_topic, escalated_at NULL)
escalations(id PK, user_id, reason, context_json,
            sent_to_s_at, resolved_at NULL)               -- reason: kb_miss|counter|manual
reports(id PK, week_start, week_end, payload_json, sent_at)
audit_log(id PK, ts, actor, action, target)
```

## 5. State Machine (per user)

```
IDLE
 ├─ reminder fired ──▶ AWAITING_CHECKIN
 │     ├─ reply within 12h ──▶ IDLE
 │     ├─ 12h nudge sent ─────▶ AWAITING_CHECKIN
 │     └─ 24h no reply ───────▶ MISSED ──▶ IDLE
 │
 └─ user msg ──▶
       ├─ crisis keywords detected ──▶ safe-messaging reply (no escalation)
       ├─ IN_QA
       │     ├─ KB hit (score>=70) + 👍/positive ──▶ IDLE (counter=0)
       │     ├─ KB hit + 👎/negative, counter<10 ────▶ IN_QA (counter++)
       │     ├─ KB miss ──▶ Gemini RAG (empathetic, no KB restriction)
       │     │     ├─ 👍/positive ─▶ IDLE (counter=0, auto-promote KB, notify S)
       │     │     └─ 👎/negative ─▶ IN_QA (counter++)
       │     └─ counter==10 ────────▶ ESCALATED
       └─ tz onboarding reply ──▶ sets users.tz, IDLE

ESCALATED ── S /resolve ──▶ IDLE (counter=0)
```

## 6. Functional Modules

### 6.1 Onboarding
`/start` registers the user and prompts for timezone. If the user replies with a
valid IANA timezone name (validated with `pytz`), `users.tz` is updated.
Invalid or missing reply is silently ignored; the default `DEFAULT_TZ` is kept.
The tz prompt intercept lives in `handlers/onboarding.handle_tz_reply()` and
is checked at the very top of the free-text message handler before KB lookup.

### 6.2 Proactive Reminders
APScheduler fires per active task → bot sends ping with mood-scale inline keyboard (😣😕😐🙂😄) → waits up to 24h. At 12h: gentle nudge. At 24h: mark `missed`. On bot restart: scan pending check-ins older than window and mark `missed`; re-arm future jobs from `tasks` table.

`/pause` now calls `scheduler().pause_job()` for each active task job in addition to flipping `users.status`. `/resume` calls `scheduler().resume_job()`. This prevents check-ins from firing while paused even if the bot restarts.

### 6.3 Crisis Pre-filter
Before any KB lookup or LLM call, `handlers/qa._is_crisis(text)` checks for a
list of EN+VI suicide/self-harm keywords. On match, the bot sends a safe-messaging
reply with crisis hotline numbers and returns immediately — no LLM is invoked,
no escalation is triggered (the bot handles it directly).

Crisis keywords are defined in `handlers/qa._CRISIS_KEYWORDS`.

### 6.4 Q&A / KB Lookup
Free-text user message → normalize → `KBRetriever.search(query, top_k=5)` returning `[(entry, score)]`.
- If `top1.score >= FUZZY_THRESHOLD (70)` → direct answer from KB.
- Else → Gemini RAG (see 6.6).

### 6.5 Satisfaction Counter (hybrid)
- After every bot answer, append inline 👍 / 👎 buttons.
- Free-text replies are also classified by `services.satisfaction.classify(text)` — keyword/regex rules in EN+VI:
  - Positive: `thanks|got it|helped|that works|tốt|cảm ơn|hiểu rồi|ổn rồi`
  - Negative: `still stuck|not really|doesn't help|didn't work|tried that|no|chưa được|không giúp|vẫn vậy`
- `+1` to counter on negative; `0` on positive; no change on neutral.
- Counter resets on positive, on topic change, after escalation, or after 24h of no Q&A.

### 6.6 Gemini RAG Fallback
Triggered on KB miss. LLM is instructed to:
- Reply in the user's detected language (VI or EN).
- For emotional sharing: respond with empathy first, no hedging.
- Use KB CONTEXT as reference; fall back to general wellness principles.
- Max 120 words. Conversational tone.

Reply prefixed with `💡 Gợi ý từ Soul Coach:` + 👍/👎 buttons.
No `parse_mode` — LLM text may have unbalanced markdown.

- 👍 → reset counter + **auto-promote to KB** (category="general") + notify S via DM.
- 👎 → `counter++`, ask for more context, keep trying. Escalate only when `counter >= SAT_THRESHOLD (10)`.

### 6.7 Escalation
Three triggers, all produce a structured DM to S:

```
🚨 Escalation — @username (uid 12345)
Reason: kb_miss | counter | manual
Last 5 turns:
  U (10:01): ...
  B (10:01): ...
  ...
[Take over]   [Mark resolved]
```

S can `/resolve <user_id>` to close. While escalated, bot stays silent on automated Q&A for that user (reminders still fire).

### 6.8 Weekly Report
Cron Sunday 18:00 (S timezone). Aggregates the past 7 days:
- Per-user: check-in compliance %, mood trend (avg), interaction count, escalations, kb_candidates pending promotion.
- Aggregate: top KB hits, top KB misses, blocked users.
- Format: markdown table in DM + JSON attachment (machine-readable archive).
- Snippets are redacted by default (first 60 chars + hash). S can run `/transcript <user_id> <YYYY-WW>` to view verbatim — this is logged in `audit_log`.

### 6.9 Health Endpoint
`services/health.py` starts a daemon HTTP thread on `HEALTH_PORT` (default 8080).
`GET /health` → `200 ok`. Suitable for UptimeRobot "HTTP" monitor.
Started in `main.py` before the Telegram poller.

## 7. Commands

| Command | Who | Purpose |
|---|---|---|
| `/start` | U | Register, onboarding + timezone prompt |
| `/help` | U | Usage |
| `/tasks` | U | List my reminders |
| `/addtask <title> | <cron>` | U | Add reminder |
| `/removetask <id>` | U | Remove |
| `/pause` `/resume` | U | Mute/unmute reminders (actually suspends scheduler jobs) |
| `/talk_to_human` | U | Manual escalation |
| `/report` | S | On-demand weekly report |
| `/resolve <user_id>` | S | Close escalation |
| `/transcript <user_id> [YYYY-WW]` | S | View verbatim history |
| `/users` | S | Active user list |
| `/kb_add <cat> | <q> | <a> | <kw>` | S | Add KB entry |
| `/kb_list [cat]` | S | Browse |
| `/kb_edit <id> <field>=<value>` | S | Update entry |
| `/kb_del <id>` | S | Delete entry |
| `/kb_promote <interaction_id>` | S | Manually promote LLM reply to KB |
| `/settask <user_id> | <title> | <cron>` | S | Assign reminder to a user |

## 8. Configuration (env vars)

| Var | Default | Notes |
|---|---|---|
| `TELEGRAM_TOKEN` | — | required |
| `SUPERVISOR_CHAT_ID` | — | required |
| `GEMINI_API_KEY` | — | required for RAG fallback |
| `GEMINI_MODEL` | `gemini-1.5-flash` | |
| `DB_PATH` | `data/soul_coach.db` | |
| `DEFAULT_TZ` | `Asia/Ho_Chi_Minh` | fallback if user doesn't set tz |
| `REMINDER_NUDGE_HOURS` | `12` | |
| `REMINDER_MISS_HOURS` | `24` | |
| `REPORT_CRON` | `0 18 * * SUN` | S timezone |
| `FUZZY_THRESHOLD` | `70` | rapidfuzz token_set_ratio scale 0–100 |
| `SAT_THRESHOLD` | `10` | LLM tries up to 10 times before escalating |
| `LOG_LEVEL` | `INFO` | |
| `HEALTH_PORT` | `8080` | HTTP health-check port |

## 9. Edge Cases & Reliability

- Telegram 429: exponential backoff via PTB built-in.
- DB writes wrapped in transactions; SQLite WAL mode enabled.
- KB writes are atomic; in-memory cache invalidated on every write.
- APScheduler jobs idempotent (keyed by `task_id + scheduled_for`).
- Bot restart recovers pending check-ins (mark missed if past window).
- User blocks bot → `Forbidden` caught → `users.status='blocked'` → scheduler skips.
- Crisis keywords handled before any LLM call; no escalation triggered.

## 10. Deployment (Oracle Always Free)

- Shape: **Ampere A1 Flex**, 2 OCPU / 12 GB RAM (Always-Free eligible).
- OS: Ubuntu 22.04 LTS.
- Process supervisor: `systemd` unit `soul-coach.service`.
- Anti-idle-reclamation: `keepalive.timer` runs `keepalive.sh` every 5 minutes (60s of light CPU + DB housekeeping). Targets ~20% utilization rate so 95th-percentile CPU stays above the 20% idle threshold.
- Backups: nightly local SQLite snapshot + off-host upload via `rclone` (`deploy/backup_offhost.sh`).
- Monitoring: UptimeRobot (free) hitting `GET /health` on port 8080.
- **Never click "Upgrade to Pay-As-You-Go".** Set $0.01 budget alert as guardrail. Log into console monthly to prevent account abandonment.

See `deploy/ORACLE_DEPLOY.md` for step-by-step.

## 11. Project Layout

```
Bot_The_Soul_Coach/
├── SPEC.md                  ← this file
├── README.md
├── TESTPLAN.md              ← test strategy and checklists
├── HANDOFF.md
├── requirements.txt
├── .env.example
├── .gitignore
├── config.py
├── main.py
├── schema.sql
├── kb_seed.yaml
├── db.py
├── handlers/
│   ├── onboarding.py        ← /start + timezone prompt
│   ├── tasks.py             ← /addtask /removetask /pause(fixed) /resume(fixed)
│   ├── qa.py                ← crisis filter + tz intercept + KB/LLM pipeline
│   ├── escalation.py
│   └── admin.py
├── services/
│   ├── kb.py
│   ├── llm.py
│   ├── satisfaction.py
│   ├── reminders.py
│   ├── reports.py
│   └── health.py            ← /health HTTP daemon thread
├── utils/
│   └── timez.py
├── deploy/
│   ├── ORACLE_DEPLOY.md
│   ├── soul-coach.service
│   ├── keepalive.service
│   ├── keepalive.timer
│   ├── keepalive.sh
│   └── backup_offhost.sh    ← rclone off-host backup
├── .github/
│   └── workflows/
│       └── ci.yml           ← smoke + unit on every push
└── tests/
    ├── test_smoke.py
    └── test_unit.py         ← 16 unit tests, no credentials needed
```

## 12. Known Scorer Gotcha

**Do NOT use `rapidfuzz.WRatio` for KB retrieval.** It returns 85+ even for
completely unrelated queries. Use **`token_set_ratio`** (scores 90–100 for
genuine matches, 30–50 for unrelated queries). Threshold of 70 correctly routes
obscure queries to the LLM fallback. Enforced in `services/kb.py`.
