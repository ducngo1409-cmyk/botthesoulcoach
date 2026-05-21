# Soul Coach Bot — Architecture Audit (v2.10.1)

> Honest assessment of the current system. Read this before planning v3+.

## 1. What the system is good at

✅ **Single-VM simplicity** — entire bot fits in 64MB RAM on GCP e2-micro free tier
✅ **DB-backed state** — onboarding/approval/escalation/role survive restarts
✅ **LLM continuity** — 4-model × 2-key failover (8 attempts), offline empathy fallback
✅ **Strict state-machine** — onboarding can't be bypassed even with garbage input
✅ **Cost** — Gemini free tier ~12k calls/day with failover; GCP free tier covers hosting
✅ **Crisis safety** — keyword pre-filter routes to safe-messaging template before LLM
✅ **Audit trail** — all transcript views logged; delete_user goes to audit_log
✅ **RBAC** — explicit permission matrix, easy to add roles, no hierarchy guessing

## 2. Scale ceiling & bottlenecks

### Current capacity estimate

| Users (DAU) | Comfortable? | Bottleneck if pushed |
|---|---|---|
| 1–50 | ✅ Easy | None |
| 50–500 | ✅ Fine | SQLite write contention starts but not user-visible |
| 500–2k | ⚠️ Tight | LLM blocks handler thread; KB search O(N) noticeable |
| 2k–10k | ❌ Needs refactor | Single-conn SQLite locks; in-process scheduler is SPOF |
| 10k+ | ❌ Full rewrite | Need Postgres, Celery/Redis queue, multi-instance |

### Specific bottlenecks

**SQLite single connection + RLock**
- `db._conn` is shared globally with `threading.RLock`.
- Write transactions serialize → one slow KB write blocks every other write.
- Fine for ≤ ~50 writes/sec; bad above that.
- Fix path: WAL mode is already on; for scale, migrate to Postgres + connection pool.

**KB fuzzy search O(N)**
- `rapidfuzz.process.extract` scans every active entry on every query.
- 100 entries ≈ 1ms, 1000 ≈ 5ms, 10 000 ≈ 50ms.
- No vector embeddings or inverted index.
- Fix path: maintain inverted index for keyword pre-filter, OR switch to vector retrieval (sentence-transformers + FAISS).

**LLM calls block the handler coroutine**
- `client.models.generate_content(...)` is a sync call inside async handler.
- One slow LLM call (15s+ timeout) holds up the handler — though PTB schedules
  concurrent handlers, so other users aren't blocked.
- Fix path: use `client.aio.models.generate_content(...)` (genai async API).

**APScheduler in-process**
- If bot dies mid-reminder, scheduled jobs are lost (BUT we recover orphaned
  check_ins on startup).
- Cannot scale horizontally — multi-instance would fire duplicate reminders.
- Fix path: move to Celery + Redis + Celery Beat for distributed scheduling.

**In-memory KB cache**
- `services/kb._cache` invalidated on EVERY write (full reload).
- At 10 000 entries, every `/kb_add` reloads ~5MB.
- Fix path: partial cache invalidation, or skip cache + rely on DB index.

**Pending-user notify rate limit is in-memory**
- `_pending_last_notify` dict survives only in process.
- Bot restart resets it → user might get duplicate "đang chờ" messages.
- Minor — accept as is.

### Hard limits

- **Telegram API rate limits**: 30 messages/sec to different users, 1/sec to same user. `/broadcast` to 100+ users may hit 429.
- **Gemini free tier**: 1500 RPD per model per key. 4 models × 2 keys = 12 000 calls/day.
- **GCP e2-micro**: 1 vCPU (shared), 1GB RAM. Bot uses 64MB now. Headroom ~10x.
- **SQLite DB**: scales to 100s of GB in size, but write throughput tops out at low-hundreds writes/sec on commodity disks.

## 3. Security gaps

### High priority

🔴 **Telegram token / Gemini keys in plaintext .env**
- Anyone with shell access to VM can read them.
- Fix: GCP Secret Manager or HashiCorp Vault.

🔴 **No 2FA / device verification for admin role**
- If admin's Telegram account is compromised, attacker gets full bot control.
- Fix: add an out-of-band confirmation for destructive actions (`/delete_user`,
  `/broadcast`, `/promote`).

🔴 **HTTP /health endpoint (not HTTPS)**
- Anyone scanning the VM IP can read "ok" and detect the bot.
- Currently no sensitive data exposed; still worth fronting with nginx + Let's Encrypt.

### Medium

🟡 **No DB encryption at rest**
- DB file readable by anyone with VM root.
- Contains user conversations (potentially sensitive mental-health content).
- Fix: SQLCipher (drop-in for `sqlite3`), or migrate to Postgres with TDE.

🟡 **No audit log for admin actions**
- Only `/transcript` and `/delete_user` write to `audit_log`.
- Promotions/demotions/broadcasts/dms not audited.
- Fix: add audit_log inserts to all admin command handlers.

🟡 **No tamper-proofing of audit_log**
- A compromised admin can `sqlite3 ... DELETE FROM audit_log;`
- Fix: append-only mode, or external log shipping to immutable store.

🟡 **No PII protection for crash logs**
- Tracebacks may include user messages.
- Fix: sanitize message content in logs.

### Low

🟢 **Cron commands accepted unverified** — but only by approved users with onboarding done, so realistic blast radius is small.
🟢 **No CAPTCHA on /start** — but allowlist + admin approval block abuse before any compute spent.
🟢 **No GDPR-style /delete_my_account** — user can ask admin via /talk_to_human.

## 4. Reliability gaps

| Issue | Impact | Fix |
|---|---|---|
| Single VM SPOF | Bot down → no reminders fire | Multi-region with failover, or accept it |
| Cron-based DB backup | Up to 24h data loss window | Use SQLite Lite Replication or continuous backup |
| No in-flight LLM cancel on shutdown | User waits forever if bot restarts mid-reply | Add asyncio timeout |
| No retry queue for failed broadcasts | One Telegram 429 kills the broadcast | Persist queue, retry with backoff |
| Logs only on local disk | If VM dies, logs lost | Ship to GCP Logging |
| No alerting beyond UptimeRobot | Silent failures (crashed scheduler) go unnoticed | Add `/debug` self-check + alert if escalations pile up |

## 5. UX gaps

### User-facing

- ❌ No `/snooze <task_id> <duration>` — pause one reminder for X hours/days
- ❌ No `/mood` chart for user (show their own trend)
- ❌ No `/export` (download own conversation history)
- ❌ No `/forget` (GDPR-style account deletion)
- ❌ No reminder snippets ("Skip today" button on check-in)
- ❌ No task categories/projects (group "morning routine" tasks)
- ❌ No DST transition warning
- ❌ No "best time" suggestion for tasks based on past compliance

### Admin-facing

- ❌ No bulk operations (approve all pending matching filter)
- ❌ No paginated `/users` (3500 char limit means ~30 users max in one message)
- ❌ No `/audit_log` viewer command
- ❌ No `/stats` (compliance %, top-mood users, KB hit rate)
- ❌ No `/onboarding_followup` (nudge users stuck in onboarding)

### Bot intelligence

- ❌ KB retrieval is fuzzy-only (no semantic similarity)
- ❌ LLM has no long-term memory beyond last 2 turns
- ❌ No personalization (LLM doesn't know user's preferred name/style)
- ❌ No proactive nudges based on mood trend (e.g. "mood ↓ this week, want to talk?")

## 6. Architecture lock-in

- 🔒 **Telegram-specific**: 80% of handler code references PTB types directly.
  Porting to Discord/Slack would require a platform abstraction layer.
- 🔒 **SQLite-coupled**: raw SQL strings everywhere. No ORM. Migration to Postgres
  needs DB driver swap + SQL dialect review.
- 🔒 **Gemini-coupled**: `services/llm.py` uses `google-genai` directly. Switching to
  OpenAI/Anthropic needs rewrite.
- 🟢 **Modular role/permission system** — easy to extend ✅
- 🟢 **Modular KB / time parsing / tz resolver** — independent of platform ✅

## 7. Roadmap (proposed)

### v2.11 — observability (1-2 days)
- Structured JSON logging (replace text logs)
- Sentry SDK for unhandled exceptions
- Basic Prometheus `/metrics` endpoint (counters per command, LLM tokens/min)
- Self-check in `/debug`: scheduler-alive, DB-writable, last LLM call

### v2.12 — user delight (2-3 days)
- `/snooze <id> <duration>` per-task snooze
- `/mood` user-facing mood chart (last 30 days)
- `/export` JSON dump of own data (GDPR-friendly)
- `/forget` self-service account deletion (confirm flow)
- "Skip today" button on check-in messages
- DST transition warnings

### v2.13 — admin polish (1-2 days)
- Paginated `/users` (page 1, page 2, ...)
- `/stats` global metrics
- `/audit_log` viewer
- Bulk approval (`/approve_all_pending`)
- Audit every admin command (not just transcript/delete)

### v3.0 — scale (1 week)
- Postgres backend (SQLAlchemy or direct)
- Async Gemini calls
- Celery + Redis for scheduler (multi-instance support)
- KB vector embeddings (FAISS or pgvector)
- HTTPS + nginx reverse proxy

### v3.1 — multi-platform (1-2 weeks)
- Platform abstraction layer (Telegram/Discord/Slack)
- Web dashboard for admins (read-only first)

### v3.2 — enterprise (2-3 weeks)
- SQLCipher / Postgres TDE for at-rest encryption
- GCP Secret Manager integration
- 2FA confirmation for destructive admin actions
- Tamper-proof audit log (append-only + external shipping)
- Multi-region failover

## 8. Benchmark numbers (measured 2026-05-21)

| Metric | Value | Notes |
|---|---|---|
| Cold start | ~3s | DB migrate + handler register + scheduler arm |
| /start latency | <500ms | DB insert + 2 messages |
| KB search (8 entries) | ~1ms | rapidfuzz token_set_ratio |
| LLM call (cached) | ~3s | gemini-2.5-flash-lite, RTT included |
| LLM call (cold) | ~6s | First call after restart |
| /addtask | <200ms | Cron parse + DB insert + scheduler arm |
| Memory at idle | 64MB | 3 users, 11 KB entries |
| Memory per active user | ~50KB | Estimated incl. session row + recent interactions cache |

## 9. Quick wins (do these next)

If you have 30 minutes:
1. **Audit log expansion** — add `audit_log` insert to `/promote`, `/demote`, `/broadcast`, `/delete_user`, `/revoke`
2. **Paginated /users** — show "page 1/3" with `/users page 2`

If you have 2 hours:
3. **Async LLM** — switch to `client.aio.models.generate_content`
4. **DB backup verify** — test the restore path actually works
5. **Telegram rate-limit handling** — wrap `/broadcast` in token-bucket

If you have a day:
6. **Move secrets to GCP Secret Manager**
7. **Add Sentry** for exception tracking
8. **Add `/snooze`** — high user value, low complexity
