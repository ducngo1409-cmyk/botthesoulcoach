# Soul Coach Bot — Test Plan (v2.9)

Two automated test suites cover all logic that doesn't require live credentials.
Manual integration tests are listed for first-run verification before going to production.

---

## 1. Automated tests (no credentials needed)

### 1.1 Smoke tests — `tests/test_smoke.py`

| # | Check |
|---|---|
| 1 | All modules import without error |
| 2 | `db.init_db()` creates 10 tables; `_migrate()` adds `kb_entries.status` if missing |
| 3 | KB seed loads ≥ 8 entries, all `status='active'` |
| 4 | KB search — Vietnamese hit cases each score ≥ 65 |
| 5 | KB search — three obscure off-topic queries each score < 65 |
| 6 | `satisfaction.classify` — 5 cases EN+VI |
| 7 | Satisfaction counter increment / reset cycle |
| 8 | KB CRUD round-trip: add → get → edit → delete |
| 9 | Cron + friendly time parser (`daily 22:30`, `weekdays 9:00`, `every 6 hours`) |
| 10 | Timezone alias resolver (`Hanoi` → `Asia/Ho_Chi_Minh`, `+7` → `Etc/GMT-7`) |
| 11 | `kb.search()` ignores `status='pending'` entries |
| 12 | `kb.has_similar()` returns existing entry above threshold |
| 13 | `kb.extract_keywords()` strips VI+EN stopwords |
| 14 | `kb.approve()` flips pending → active and updates cache |

```bash
python -m tests.test_smoke
```

### 1.2 Unit tests — `tests/test_unit.py`

| Class | Tests |
|---|---|
| `TestCrisisFilter` | EN+VI matches; non-crisis returns False |
| `TestCrisisHandler` | Crisis reply skips LLM; non-crisis reaches KB |
| `TestHealthEndpoint` | `GET /health` → 200 `ok`; unknown → 404 |
| `TestTimezonePrompt` | New user prompted; valid tz → DB updated; invalid → warning |
| `TestReminderCore` | `_mark_missed` only on pending; paused users skipped; check_ins row created for active |
| `TestPauseResume` | `/pause` flips status AND `pause_job`; `/resume` mirrors |
| `TestLLMFailover` | 429 on key 0 → key 1 attempted; 5xx triggers failover; empty response triggers failover; all-fail raises `LLMQuotaError` if any 429 else `LLMError` |
| `TestKBPending` | Auto-promote inserts `status='pending'`; pending excluded from `search()`; dedup gate skips on similar match; approve flips to active |

```bash
python -m tests.test_unit
```

### 1.3 Run both

```bash
python -m tests.test_smoke && python -m tests.test_unit
```

### 1.4 CI

`.github/workflows/ci.yml` runs both jobs on every push/PR using Python 3.13.

---

## 2. Manual integration tests (live credentials)

### Setup

```bash
cp .env.example .env
# Fill TELEGRAM_TOKEN, SUPERVISOR_CHAT_ID, GEMINI_API_KEY (and optional GEMINI_API_KEY_2)
python main.py
```

### 2.1 Bot startup
- [ ] Bot starts; `logs/` dir created
- [ ] `GET :8080/health` → `200 ok`
- [ ] DB at `data/soul_coach.db`; `_migrate()` log line shows if column added

### 2.2 Onboarding (v2.7)
- [ ] `/start` → "Xin chào X, mình là *Soul Coach* của bạn." + tz prompt with city examples
- [ ] Reply `Hanoi` → `Đã đặt múi giờ Asia/Ho_Chi_Minh`
- [ ] Reply `Tokyo` → `Asia/Tokyo`
- [ ] Reply `+7` → `Etc/GMT-7`
- [ ] Reply `not a real place` → friendly retry message, user stays in tz-awaiting state
- [ ] Reply `skip` → keeps default, no further prompt
- [ ] `/tz` (no arg) → shows current tz
- [ ] `/tz Singapore` → updates to Asia/Singapore
- [ ] `/start` again → welcome-back, no tz prompt

### 2.3 KB Q&A
- [ ] `tôi không tập trung được` → KB direct answer + 👍/👎
- [ ] 👍 → `🌟 Vui vì mình giúp được bạn!`
- [ ] Obscure question → LLM reply with `💡 Gợi ý từ Soul Coach:` prefix
- [ ] Emotional sharing (e.g. "thời tiết âm u quá tôi cũng thấy buồn") → empathetic LLM response
- [ ] 👎 nine times → bot keeps trying with "kể thêm về tình huống"
- [ ] 👎 tenth → escalation card sent to S

### 2.4 Crisis filter
- [ ] `tự tử` → crisis reply with hotline; no LLM call; no escalation; no `log_out` row

### 2.5 LLM failover & continuity
- [ ] Tail log: `tail -f logs/bot.err.log | grep tokens` — every LLM call shows `LLM tokens [<model> key <n>]: in=… out=…`
- [ ] Force 429 (use known-exhausted key as primary) → log shows failover to key 1 then next model; user still gets reply
- [ ] Force all models down (set `GEMINI_MODEL=gemini-nonexistent`) → user sees offline empathy template + `/talk_to_human` hint (not "kỹ thuật" error)

### 2.6 KB Pending Review (NEW v2.6)
- [ ] 👍 on an LLM reply → S receives DM "📚 KB pending #N — chờ duyệt" with `✅ Approve` / `❌ Reject` buttons + extracted keywords shown
- [ ] Active KB entries: confirm pending entry NOT yet returned by `kb.search()` (test by sending similar query before approving)
- [ ] Tap `✅ Approve` → button removed, message edited to show "Approved"; entry now matches in search
- [ ] `/kb_pending` → lists pending entries
- [ ] `/kb_approve <id> sleep "ngủ, mất ngủ"` → category + keywords overridden
- [ ] `/kb_reject <id>` → entry deleted
- [ ] **Dedup gate**: 👍 on LLM reply for a question very similar to an existing active entry → no pending entry created (log: `Auto-promote skipped`)
- [ ] **Length gate**: very short user question (< 4 chars) → no pending entry created

### 2.7 Reminders + per-task control (v2.7)
- [ ] `/addtask` (no args) → shows friendly examples block
- [ ] `/addtask Thiền | daily 7:00` → confirmation with `⏰ mỗi ngày lúc 07:00` and cron `0 7 * * *`
- [ ] `/addtask Họp | weekdays 9:00` → cron `0 9 * * 1-5`
- [ ] `/addtask Uống nước | every 3 hours` → cron `0 */3 * * *`
- [ ] `/addtask Test | * * * * *` → check-in DM within 90s; mood emoji works
- [ ] **Per-task pause**: `/pause <id1>` → only that task pauses; other tasks still fire
- [ ] `/resume <id1>` → only that task resumes
- [ ] `/pause` (no arg) → 🔕 "*tất cả* N nhắc nhở"
- [ ] `/resume` (no arg) → 🔔 "đã bật lại N nhắc nhở"
- [ ] **Per-task nudge**: `/nudge <id> 0` → user gets no follow-up after missed check-in
- [ ] `/nudge <id> 6` → nudge fires 6h after check-in
- [ ] `/tasks` → shows pause state + nudge config per task
- [ ] `/removetask <id>` → removed

### 2.8 Escalation
- [ ] `/talk_to_human` → escalation card to S
- [ ] While escalated, send any text → wait-reminder (not silent)
- [ ] `/talk_to_human` again while escalated → "bạn đang trong hàng chờ"
- [ ] Tap "Mark resolved" or `/resolve <uid>` → user gets reactivation message
- [ ] Restart bot with > 24h-old unresolved escalation → auto-cleared on startup

### 2.9 Supervisor task assignment (friendly time supported)
- [ ] `/settask <uid> | Uống nước | daily 9:00` → S confirmation + user DM both show `⏰ mỗi ngày lúc 09:00`
- [ ] `/settask <uid> | Họp | weekdays 14:00` → S receives `⏰ thứ 2 đến thứ 6 lúc 14:00`
- [ ] User `/tasks` → new entry visible

### 2.10 /debug
- [ ] `/debug` snapshot includes: users count, **KB active + pending counts**, escalated sessions, open escalations, recent LLM replies

### 2.10b User management (v2.9)
- [ ] **Request-to-join (v2.8)**: Account khác /start → bạn nhận DM với 2 nút; user nhận "đã gửi yêu cầu"
- [ ] Tap ✅ Duyệt → user nhận "✅ Được chấp nhận"; nút biến mất, message edit thành "Approved"
- [ ] Tap ❌ Từ chối → user bị flag rejected, không nhận thông báo
- [ ] `/pending` → list pending users
- [ ] `/approve <id>` / `/reject <id>` từ chat → tương đương nút
- [ ] **View commands**: `/users` → list tất cả với badges; `/users pending` → chỉ pending; `/users blocked` → chỉ blocked
- [ ] `/user <id>` → profile chi tiết với stats (tasks, interactions, mood avg, last seen)
- [ ] `/user_tasks <id>` → list reminders của user
- [ ] **Access**: `/revoke <id>` (approved user) → flip to rejected, user nhận DM "đã bị thu hồi"; gate chặn từ giờ
- [ ] **Operational**: `/block <id>` → status=blocked; reminder không fire; bot không reply; `/unblock` → restore
- [ ] `/freeze <id>` → status=paused; reminder dừng nhưng bot vẫn reply chat; `/unfreeze` → resume
- [ ] **Communication**: `/dm <id> Hello` → user nhận `💌 Tin từ coach: Hello`
- [ ] `/broadcast Thông báo tuần này` → tất cả approved+active nhận `📢 Thông báo từ coach: ...`; S nhận summary "gửi thành công N/M"
- [ ] **Lifecycle**: `/reonboard <id>` → user nhận DM "admin đã reset thiết lập"; lần nhắn kế tiếp bị vào tz prompt
- [ ] `/delete_user <id>` không có "confirm" → bot yêu cầu xác nhận, hiện tên user
- [ ] `/delete_user <id> confirm` → user + tasks + interactions xóa hết; ghi audit_log; jobs unschedule
- [ ] **Sanity**: `/revoke <supervisor_id>` → bot từ chối "Không thể revoke supervisor"
- [ ] `/delete_user <supervisor_id> confirm` → bot từ chối "Không thể xóa supervisor"

### 2.11 KB management (manual)
- [ ] `/kb_add test | What is X? | X is a test. | x,test`
- [ ] `/kb_list test` → shows entry
- [ ] `/kb_edit <id> answer=Updated.`
- [ ] `/kb_del <id>`

### 2.12 Weekly report
- [ ] `/report` → markdown DM + JSON attachment; pending KB count appears in stats

### 2.13 Logrotate
- [ ] After deploying `deploy/soul-coach.logrotate` → `sudo logrotate -d /etc/logrotate.d/soul-coach` runs without error
- [ ] After 1 week: `ls logs/` shows `bot.err.log.1.gz` (rotated + compressed)

### 2.14 Health & backups
- [ ] `:8080/health` reachable from UptimeRobot
- [ ] `deploy/backup_offhost.sh` produces snapshot in `~/backups/` and on remote

---

## 3. Regression checklist before merge

```
[ ] python -m tests.test_smoke  — passes (14 checks)
[ ] python -m tests.test_unit   — passes
[ ] No new imports of rapidfuzz.WRatio (use token_set_ratio only)
[ ] No new raw os.environ access — must go through config.settings()
[ ] Crisis filter keywords intact in handlers/qa.py
[ ] kb.search() still filters status='active'
[ ] _promote_to_kb still inserts status='pending' (never directly active)
[ ] LLM failover loop still continues on 429 / 5xx / empty / network — never raises mid-loop
[ ] /addtask + /settask both route time through services.timeparser.parse()
[ ] tz onboarding + /tz both route input through services.tz_aliases.resolve_tz()
[ ] _send_checkin checks both users.status AND tasks.active
```

---

## 4. Live monitoring (continuous operation)

Tail-and-grep one-liner for spotting failures fast:

```bash
gcloud compute ssh soul-coach --zone us-central1-a --command="\
  sudo tail -f /home/hallo_5ambloom/Bot_The_Soul_Coach/logs/bot.err.log \
  | grep -E 'tokens|429|5[0-9][0-9]|empty|error|escalat'"
```

What healthy traffic looks like:
```
INFO services.llm :: LLM tokens [gemini-2.5-flash-lite key 0]: in=187 out=82 total=269
```

What a transient blip looks like (auto-recovered, no user impact):
```
WARNING services.llm :: LLM 503 [gemini-2.5-flash-lite key 0] — …
INFO services.llm :: LLM tokens [gemini-2.5-flash key 0]: in=187 out=110 total=297
```

What "all attempts failed" looks like (offline empathy fired):
```
WARNING services.llm :: LLM 429 [gemini-2.0-flash key 1] — …
WARNING handlers.qa :: LLM unavailable, using offline empathy: …
```
