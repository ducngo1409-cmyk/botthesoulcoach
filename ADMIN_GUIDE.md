# Soul Coach — Admin / Staff Guide

> Hướng dẫn dành cho staff: **Admin 👑**, **Coacher 🎓**, **Service ⚙️**.
>
> Phiên bản: v2.10 (2026-05)

---

## 0. Hệ thống Role (v2.10)

Bot có 4 role, mỗi role có quyền hạn riêng (tham khảo cấu trúc Discord MEE6 / Telegram Combot):

| Role | Emoji | Quyền |
|---|---|---|
| **admin** | 👑 | Toàn quyền: user lifecycle, role management, broadcast, delete |
| **coacher** | 🎓 | Xử lý user: escalation, KB, transcript, settask, dm |
| **service** | ⚙️ | Read-only: debug, view users (cho monitoring/automation) |
| **user** | 👤 | Người dùng cuối, chỉ quản lý tài nguyên của mình |

`SUPERVISOR_CHAT_ID` được auto-set role `admin` lúc boot (bootstrap admin, không thể demote).

### Lệnh role management (admin only)

| Lệnh | Việc làm |
|---|---|
| `/promote <user_id> <role>` | Gán role. Auto-approve user nếu trước đó là pending |
| `/demote <user_id>` | Đưa user về role `user` |
| `/roles` | Liệt kê tất cả staff theo role |
| `/myrole` | Ai cũng dùng được — xem role của bản thân |

### Permission matrix

| Permission | admin | coacher | service | user |
|---|---|---|---|---|
| view_users | ✅ | ✅ | ✅ | ❌ |
| manage_users (approve/reject/revoke/block/freeze/delete/reonboard) | ✅ | ❌ | ❌ | ❌ |
| manage_roles | ✅ | ❌ | ❌ | ❌ |
| broadcast | ✅ | ❌ | ❌ | ❌ |
| dm_user | ✅ | ✅ | ❌ | ❌ |
| view_transcripts | ✅ | ✅ | ❌ | ❌ |
| handle_escalation | ✅ | ✅ | ❌ | ❌ |
| assign_task | ✅ | ✅ | ❌ | ❌ |
| manage_kb | ✅ | ✅ | ❌ | ❌ |
| review_kb_pending | ✅ | ✅ | ❌ | ❌ |
| view_debug | ✅ | ❌ | ✅ | ❌ |
| view_reports | ✅ | ✅ | ❌ | ❌ |

### Notification fan-out

Bot tự gửi nội bộ tới ALL người có quyền tương ứng:

| Sự kiện | Gửi cho |
|---|---|
| User mới /start (pending) | Tất cả `admin` |
| Escalation (kb_miss / counter / manual) | Tất cả `admin` + `coacher` |
| KB pending review | Tất cả `admin` + `coacher` |
| Quota error | `admin` (rate-limited) |

Trước đây chỉ DM `SUPERVISOR_CHAT_ID` — giờ scale được khi có nhiều admin/coacher.

---

## 1. Vai trò Supervisor

Supervisor là **một và chỉ một người**, định danh bằng `SUPERVISOR_CHAT_ID` trong `.env`. Nhiệm vụ:

1. **Cấp quyền truy cập** — quản lý allowlist `ALLOWED_USER_IDS`
2. **Duyệt KB** — phê duyệt entries pending khi bot học từ phản hồi 👍 của user
3. **Xử lý escalation** — khi bot không giúp được user, S nhận DM và tiếp quản
4. **Theo dõi hệ thống** — qua `/debug`, `/report`, log file, UptimeRobot
5. **Quản lý task của user** — gán nhắc nhở qua `/settask`

S đăng nhập bằng chính tài khoản Telegram của mình. Để biết `SUPERVISOR_CHAT_ID`: DM bot [@userinfobot](https://t.me/userinfobot).

---

## 2. Cấp quyền truy cập (Request-to-Join)

### 2.1 Cách hoạt động (v2.8)

Bất kỳ ai search được bot trên Telegram đều có thể gõ `/start`, nhưng:

1. User mới được tạo trong DB với `access_status = 'pending'`
2. Bot trả lời user: "🔒 Yêu cầu đã được gửi đến admin để duyệt..."
3. **Bạn (S) nhận DM ngay** với 2 nút inline:
   ```
   🆕 Yêu cầu truy cập mới
   👤 Nguyễn Văn A
   🆔 1338639986
   📱 @vana
   
   [✅ Duyệt]  [❌ Từ chối]
   ```
4. Bạn tap **Duyệt** → user nhận thông báo và có thể dùng bot
5. Bạn tap **Từ chối** → user bị flag `rejected`, không dùng được nữa

### 2.2 Lệnh quản lý approval

| Lệnh | Việc làm |
|---|---|
| `/pending` | Liệt kê user đang chờ |
| `/approve <user_id>` | Duyệt (giống tap nút Duyệt) |
| `/reject <user_id>` | Từ chối (giống tap nút Từ chối) |
| `/users` | Xem tất cả user với badge ✅⏳🚫 |

### 2.3 Pending users gõ gì cũng bị chặn

- Mỗi 30 giây user pending nhắn gì → bot trả "⏳ đang chờ duyệt" (rate limit)
- /start lại → bot nhắc lại lần nữa
- Mọi action khác (callback, command) → drop silently

### 2.4 Bỏ qua approval (chế độ dev/test)

Trong `.env`:
```
REQUIRE_APPROVAL=0
```

Restart bot — mọi user mới sẽ auto-approved khi /start. Dùng cho dev/test only.

### 2.5 Supervisor luôn được auto-approved

Bạn (S) không bao giờ rơi vào pending — `SUPERVISOR_CHAT_ID` bypass mọi gate.

---

## 2bis. Quản lý user (v2.9)

Bot có 2 trục trạng thái độc lập:

| Trục | Cột DB | Giá trị | Tác dụng |
|---|---|---|---|
| **Access** | `access_status` | `pending` / `approved` / `rejected` | Quyền truy cập tổng thể |
| **Operational** | `status` | `active` / `paused` / `blocked` | Bot có gửi reminder/tin không |

User chỉ dùng được bot khi `access_status='approved'` AND `status='active'`.

### 2bis.1 View / inspect

| Lệnh | Việc làm |
|---|---|
| `/users` | List tất cả user với badge ✅⏳🚫 và 🔕⛔ |
| `/users pending` | Chỉ pending |
| `/users approved` / `rejected` | Theo access state |
| `/users active` / `paused` / `blocked` | Theo operational state |
| `/user <id>` | Profile chi tiết + stats (tasks, interactions, escalations, mood avg, last seen) |
| `/user_tasks <id>` | Liệt kê task của user |
| `/transcript <id> [YYYY-WW]` | Lịch sử hội thoại |

Output mẫu `/user 12345`:
```
👤 Nguyễn Văn A
🆔 12345
🕐 Asia/Ho_Chi_Minh
✅ access: approved
🟢 status: active
📋 onboarding: ✅ done
📅 joined: 2026-05-05 17:20:00

📊 Stats
• Tasks: 3
• Interactions: 142
• Escalations: 1 (0 open)
• Mood: avg 3.7/5 (24 ratings)
• Last interaction: 2026-05-21 14:32:15
```

### 2bis.2 Access management

| Lệnh | Effect |
|---|---|
| `/approve <id>` | `pending → approved`, user nhận DM "đã được chấp nhận" |
| `/reject <id>` | `pending → rejected` (không notify) |
| `/revoke <id>` | `approved → rejected`, user nhận DM "đã bị thu hồi" |

### 2bis.3 Operational state (không đụng access)

| Lệnh | Effect |
|---|---|
| `/block <id>` | `status=blocked`, bot ngưng mọi outgoing (reminder + reply) |
| `/unblock <id>` | `blocked → active` |
| `/freeze <id>` | `status=paused` + APScheduler pause tất cả task job của user |
| `/unfreeze <id>` | `paused → active` + resume jobs |

Phân biệt:
- **revoke** = thu hồi quyền truy cập (mạnh nhất, user thấy thông báo)
- **block** = bot không gửi gì cho user (vẫn approved, nhưng câm)
- **freeze** = chỉ tắt reminder, user vẫn nhắn được bot bình thường

### 2bis.4 Communication

| Lệnh | Việc làm |
|---|---|
| `/dm <id> <msg>` | Bot gửi DM cho 1 user — hiển thị `💌 Tin từ coach: <msg>` |
| `/broadcast <msg>` | Gửi cho TẤT CẢ user approved+active (trừ S) — `📢 Thông báo từ coach: <msg>`. Báo cáo số thành công/thất bại |

> ⚠️ `/broadcast` không có undo — soạn nội dung kỹ trước.

### 2bis.5 Lifecycle

| Lệnh | Việc làm |
|---|---|
| `/reonboard <id>` | Reset onboarding (`onboarded=0`). User phải set tz lại lần nhắn kế tiếp |
| `/delete_user <id>` | Yêu cầu xác nhận: trả về tên user + lệnh `confirm` |
| `/delete_user <id> confirm` | **XÓA HẲN** user + cascade tasks/interactions/sessions/escalations/check_ins. Ghi audit log. Không hồi phục được |

### 2bis.6 Lifecycle hoàn chỉnh của 1 user

```
[Search bot]
     ↓ /start
[pending]──/approve──▶[approved + onboarding]──tz set──▶[approved + active]
     │ /reject               │                                │
     ▼                       ▼ /reonboard                     │
[rejected]              [approved + onboarding]               │
                                                              ▼
                                                    /freeze ──▶ [paused] ──/unfreeze──▶
                                                    /block ──▶ [blocked] ──/unblock──▶
                                                    /revoke ──▶ [rejected]
                                                    /delete_user confirm ──▶ (gone)
```

---

## 3. KB Pending Review Queue

### 3.1 Cách hoạt động

Khi user 👍 vào một câu trả lời AI, bot tạo entry `pending` trong `kb_entries`:
- Entry **không xuất hiện** trong fuzzy search cho đến khi S approve
- S nhận DM ngay với 2 nút inline: **✅ Approve** / **❌ Reject**
- Bot lọc trùng tự động (dedup gate fuzzy ≥ 75) — không tạo entry nếu đã có entry tương tự
- Bot tự trích keywords từ câu hỏi (bỏ stopword VI + EN, giữ 5 token đặc trưng)

### 3.2 Duyệt qua nút inline (nhanh nhất)

DM của bạn sẽ trông như:
```
📚 KB pending #42 — chờ duyệt

❓ làm sao để bớt căng thẳng khi deadline cận
🤖 Mình hiểu, deadline thật áp lực. Thử kỹ thuật 4-7-8: hít 4s, giữ 7s, thở ra 8s...
🔑 keywords: bớt, căng, thẳng, deadline, cận

[✅ Approve]  [❌ Reject]
```

### 3.3 Duyệt qua lệnh

| Lệnh | Việc làm |
|---|---|
| `/kb_pending` | Xem tất cả entries đang chờ duyệt |
| `/kb_approve <id>` | Approve y nguyên |
| `/kb_approve <id> stress` | Approve và đổi category sang `stress` |
| `/kb_approve <id> stress "căng thẳng, áp lực"` | Approve, đổi category + keywords |
| `/kb_reject <id>` | Xóa entry pending |

### 3.4 Tại sao phải review?

Auto-add KB không qua review sẽ gây:
- **Drift**: bot trả lời từ chính câu trả lời cũ của nó → hallucination tích lũy
- **Duplicates**: "tôi buồn", "buồn quá", "đang buồn" thành 3 entry riêng
- **Quality drop**: KB rác kéo fuzzy threshold xuống → false positive

Pending queue đảm bảo KB chỉ chứa câu trả lời S đã đánh giá là đúng.

---

## 4. Escalation

### 4.1 Khi nào escalate

Bot tự DM S khi:
- User gõ `/talk_to_human` (manual)
- User 👎 đủ 10 lần liên tiếp (counter)
- (Không còn tự escalate trên KB miss — bot cố gắng giúp đến cuối)

### 4.2 Nội dung DM escalation

```
🚨 Escalation
User: Nguyen Van A (uid 1338639986)
Reason: 🔁 5 unsatisfied responses in a row

Last turns:
👤 2026-05-21 14:30  tôi vẫn không tập trung được
🤖 2026-05-21 14:30  Thử kỹ thuật Pomodoro...
👤 2026-05-21 14:32  chưa được, vẫn không tập trung
🤖 2026-05-21 14:32  Hay là kiểm tra môi trường...
👤 2026-05-21 14:35  vẫn vậy

[✅ Mark resolved]
```

### 4.3 Xử lý

| Lệnh | Việc làm |
|---|---|
| Tap nút `✅ Mark resolved` | Đóng escalation, user nhận tin "sẵn sàng hỗ trợ lại" |
| `/resolve <user_id>` | Như trên (dùng khi tap không tiện) |
| `/transcript <user_id>` | Xem 50 turn gần nhất verbatim |
| `/transcript <user_id> 2026-21` | Xem turn tuần thứ 21 năm 2026 |

Trong khi user `escalated`:
- User nhắn bất cứ gì → bot gửi "đang chờ coach" thay vì im lặng
- Bot tự động clear escalation > 24h chưa resolve (khi restart)

### 4.4 Cách trò chuyện với user khi escalated

Bot không relay — bạn nhắn riêng cho user qua Telegram bằng chính tài khoản của bạn. Sau khi xong, gõ `/resolve <user_id>` để bot tiếp tục.

---

## 5. Quản lý task user

### 5.1 Gán task cho user

```
/settask <user_id> | <tên> | <giờ>
```

Ví dụ:
```
/settask 1338639986 | Thiền sáng | daily 7:00
/settask 1338639986 | Báo cáo | weekdays 17:30
/settask 1338639986 | Họp team | every monday 9:00
/settask 1338639986 | Đi bộ | weekends 6:30
```

`<giờ>` hỗ trợ cả friendly format và cron 5-trường (xem [USER_GUIDE.md](USER_GUIDE.md) phần 3).

User nhận DM thông báo task mới và có thể `/pause`, `/resume`, `/nudge` như task của họ.

### 5.2 Lấy `user_id` của user

```
/users
```

Hiển thị danh sách tất cả user đã đăng ký:
```
👥 Users
• `1338639986` *Đức Ngô* — active — joined 2026-05-05 10:00:00
• `8796539835` *Phương Anh* — active — joined 2026-05-05 17:20:00
```

---

## 6. Theo dõi hệ thống

### 6.1 `/debug` — snapshot trực tiếp

```
🔧 Bot Debug Snapshot
👥 Users: 3
📚 KB active: 12 | pending: 4
🔴 Escalated sessions: 0

🚨 Open escalations (0):
   (none)

💬 Recent LLM replies:
   2026-05-21 19:32: Mình hiểu cảm giác này, đôi khi…
   2026-05-21 19:30: Hãy thử kỹ thuật Pomodoro...

/resolve <uid> to clear escalation
tail -f logs/bot.err.log to monitor live
```

### 6.2 Tail log trên VM

```bash
gcloud compute ssh soul-coach --zone us-central1-a

# Toàn bộ log
sudo tail -f /home/hallo_5ambloom/Bot_The_Soul_Coach/logs/bot.err.log

# Chỉ event LLM (token, fail, failover)
sudo tail -f .../logs/bot.err.log | grep -E 'tokens|429|5[0-9][0-9]|empty|escalat'

# Chỉ error
sudo tail -f .../logs/bot.err.log | grep -E 'ERROR|Traceback'
```

Cảm nhận healthy:
- `LLM tokens [gemini-2.5-flash-lite key 0]: in=187 out=82 total=269` — 1 call thành công
- `LLM 503 [...] — failing over` rồi ngay sau là `tokens` line trên model/key khác → tự khôi phục

### 6.3 UptimeRobot

UptimeRobot ping `http://<vm-ip>:8080/health` mỗi 5 phút (gói free). Có alert email nếu bot down.

---

## 7. Báo cáo tuần

### 7.1 Tự động

Mỗi chủ nhật 18:00 (giờ S timezone) bot DM báo cáo tuần:
- Compliance per user (check-in trả lời / tổng số)
- Mood trend
- Top KB hits / misses
- Số escalation
- Pending KB count

### 7.2 Theo yêu cầu

```
/report
```

Bot sẽ tạo và DM ngay.

### 7.3 Xem transcript user

```
/transcript <user_id>             — 50 turn gần nhất
/transcript <user_id> 2026-21     — tuần 21 năm 2026
```

Mọi lần xem transcript đều ghi vào `audit_log` (audit trail).

---

## 8. Quản lý KB thủ công

### 8.1 Thêm entry mới

```
/kb_add <category> | <question> | <answer> | <keywords>
```

Ví dụ:
```
/kb_add anxiety | làm sao bớt lo lắng | Thử bài tập thở 4-7-8... | lo lắng, anxiety, áp lực
```

Entry này active luôn (không qua pending queue).

### 8.2 Browse + edit

```
/kb_list                     — toàn bộ KB
/kb_list anxiety             — chỉ category 'anxiety'
/kb_edit 42 answer=Câu mới   — đổi answer
/kb_edit 42 keywords=a,b,c   — đổi keywords
/kb_edit 42 category=stress  — đổi category
/kb_del 42                   — xóa
```

### 8.3 Promote từ interaction cũ

```
/kb_promote <interaction_id>
```

Lấy 1 câu trả lời LLM cũ (xem `id` qua `/transcript`) và push vào KB. Bot sẽ dùng câu user trước đó làm question.

---

## 9. Deploy & maintenance

### 9.1 Update code

```bash
gcloud compute ssh soul-coach --zone us-central1-a
sudo -u hallo_5ambloom bash -lc 'cd ~/Bot_The_Soul_Coach && git pull origin main'
sudo systemctl restart soul-coach
sudo systemctl status soul-coach
```

### 9.2 Đổi env var

```bash
sudo nano /home/hallo_5ambloom/Bot_The_Soul_Coach/.env
sudo systemctl restart soul-coach
```

Các env quan trọng (xem chi tiết [SPEC.md](SPEC.md#8-configuration-env-vars)):

| Var | Mặc định | Ý nghĩa |
|---|---|---|
| `ALLOWED_USER_IDS` | _(empty)_ | Comma-separated allowlist; rỗng = mở |
| `REQUIRE_ONBOARDING` | `1` | `0` để cho phép user bỏ qua tz |
| `GEMINI_MODEL` | `gemini-2.5-flash-lite,gemini-2.5-flash,gemini-2.0-flash-lite,gemini-2.0-flash` | Failover chain |
| `GEMINI_API_KEY_2` | _(empty)_ | Key dự phòng (account khác) |
| `SAT_THRESHOLD` | `10` | Số 👎 liên tiếp trước khi escalate |
| `REMINDER_NUDGE_HOURS` | `12` | Nudge mặc định (user có thể override per-task) |

### 9.3 Backup

`deploy/backup_offhost.sh` chạy nightly qua cron → snapshot `data/soul_coach.db` rclone lên Google Cloud Storage. Xem `deploy/GCP_DEPLOY.md` cho thiết lập.

### 9.4 Logrotate

`deploy/soul-coach.logrotate` rotate `bot.err.log` mỗi tuần, giữ 4 tuần. Cài 1 lần:
```bash
sudo cp deploy/soul-coach.logrotate /etc/logrotate.d/soul-coach
```

### 9.5 Khi nào restart

| Tình huống | Cần restart? |
|---|---|
| Sửa code Python | ✅ |
| Sửa `.env` | ✅ |
| Sửa `schema.sql` | ✅ (migrate tự chạy lúc boot) |
| Thêm KB qua `/kb_add` | ❌ (cache invalidate ngay) |
| Approve KB pending | ❌ |
| Đổi `GEMINI_API_KEY` | ✅ |

---

## 10. Troubleshooting

| Triệu chứng | Nguyên nhân thường gặp | Xử lý |
|---|---|---|
| Bot im lặng với user | (a) User bị escalated, (b) LLM quota fail | Check `/debug` xem có escalated session; check log có `429` |
| User không vào được bot | Họ không trong `ALLOWED_USER_IDS` | Thêm `user_id` vào `.env`, restart |
| Bot trả lời cộc lốc | LLM trả empty / model thinking ăn token | Kiểm tra `GEMINI_MODEL` (tránh 2.5-flash mặc định thinking) |
| Reminder không fire | Task bị `/pause` (id riêng) HOẶC user status `paused` | `/users` xem status; `/tasks` user xem ✅/⏸ |
| KB hit sai | Threshold quá thấp HOẶC entries rác | Tăng `FUZZY_THRESHOLD`, dọn pending queue |
| Bot crash loop | Migration lỗi HOẶC DB lock | `journalctl -u soul-coach -n 100`; backup DB rồi xem error |

---

## 11. Cấu trúc dữ liệu

Tóm tắt 10 table chính (chi tiết: `schema.sql`):

| Table | Mục đích |
|---|---|
| `users` | Đăng ký user, tz, status (active/paused/blocked) |
| `tasks` | Reminder definitions + per-task nudge config |
| `check_ins` | Mỗi lần bot ping check-in → status pending/answered/missed |
| `interactions` | Tất cả tin nhắn in/out (audit + transcript) |
| `kb_entries` | KB entries với status active/pending |
| `sessions` | Per-user runtime state: sat_counter, escalated_at |
| `escalations` | Lịch sử escalation chi tiết |
| `reports` | Lưu báo cáo tuần đã gửi (archive) |
| `audit_log` | S đã làm gì khi nào (transcript view, KB approve, ...) |

---

## 12. Tài liệu khác

- [USER_GUIDE.md](USER_GUIDE.md) — hướng dẫn cho user cuối
- [SPEC.md](SPEC.md) — spec đầy đủ cho dev
- [TESTPLAN.md](TESTPLAN.md) — test strategy
- [deploy/GCP_DEPLOY.md](deploy/GCP_DEPLOY.md) — deploy step-by-step
- [README.md](README.md) — quick start cho dev

---

## 13. Liên hệ kỹ thuật

Nếu bot crash hoặc có lỗi không xử lý được, gửi 3 thứ:
1. Output của `/debug`
2. 50 dòng cuối log: `sudo tail -50 ~/Bot_The_Soul_Coach/logs/bot.err.log`
3. Output của `systemctl status soul-coach`

cho dev team.
