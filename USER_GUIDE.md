# Soul Coach — Hướng dẫn người dùng

> Hướng dẫn dành cho người dùng cuối. Đọc trong 5 phút là dùng được hết.

## 1. Bot là gì?

Soul Coach là người bạn nhắc nhở và lắng nghe trên Telegram. Bot giúp bạn:
- 🌱 **Duy trì thói quen** — đặt lịch nhắc nhở (thiền, uống nước, đi bộ, học bài…)
- 💬 **Trò chuyện** — tâm sự bất cứ điều gì, bot lắng nghe và gợi ý (dùng AI)
- 🧠 **Theo dõi cảm xúc** — chấm điểm 😣😕😐🙂😄 mỗi lần check-in
- 🤝 **Kết nối coach thật** — khi cần, bot sẽ chuyển bạn đến người thật

## 2. Bắt đầu trong 30 giây

```
/start
```

Bot sẽ chào bạn và hỏi *bạn đang ở thành phố nào*. Trả lời tên thành phố hoặc quốc gia bằng tiếng Anh hoặc tiếng Việt — bot hiểu cả hai:

- `Hanoi` / `Hà Nội` / `HCM` / `Saigon` / `Vietnam` / `VN`
- `Tokyo` / `Singapore` / `Bangkok` / `London` / `Paris` / `New York`
- Hoặc múi giờ trực tiếp: `+7`, `UTC+9`, `GMT-5`
- Hoặc nhắn `skip` để giữ mặc định Việt Nam

> ⚠️ **Bạn phải xong bước này** trước khi dùng lệnh khác. Nếu lỡ gõ sai, bot sẽ hỏi lại — bạn có thể trả lời nhiều lần cho đến khi đúng.

## 3. Thêm nhắc nhở (`/addtask`)

Cú pháp: `/addtask <tên> | <giờ>`

### 3.1 Cách đơn giản (khuyến nghị)
```
/addtask Thiền sáng | daily 7:00
/addtask Uống nước | every 3 hours
/addtask Báo cáo  | weekdays 17:30
/addtask Đi bộ    | weekends 6:30
/addtask Họp team | every monday 9:00
/addtask Đọc sách | t2 t4 t6 21:00
```

| Cụm từ | Ý nghĩa |
|---|---|
| `daily 22:30` | Mỗi ngày lúc 22:30 |
| `weekdays 9:00` | Thứ 2 đến thứ 6 lúc 9:00 |
| `weekends 10:00` | Thứ 7 và chủ nhật lúc 10:00 |
| `every monday 8:00` | Mỗi thứ 2 lúc 8:00 |
| `t2 t4 t6 7:15` | Thứ 2, 4, 6 lúc 7:15 |
| `every 6 hours` | Mỗi 6 giờ |
| `every 30 minutes` | Mỗi 30 phút |

### 3.2 Cách nâng cao — cron 5 trường
Nếu bạn quen với cron Unix:
```
/addtask Thiền | 0 7 * * *      → mỗi ngày 7:00
/addtask Họp  | 30 22 * * *     → mỗi ngày 22:30
/addtask Báo cáo | 0 17 * * 1-5 → T2-T6 lúc 17:00
/addtask Đi bộ | 30 6 * * 0,6   → cuối tuần 6:30
```

Cú pháp: `phút giờ ngày tháng thứ-trong-tuần`

## 4. Xem nhắc nhở (`/tasks`)

```
/tasks
```

Bot sẽ liệt kê tất cả nhắc nhở của bạn:
```
📌 Nhắc nhở của bạn
✅ #1 Thiền sáng — `0 7 * * *`
✅ #2 Họp team  — `0 9 * * 1` • nhắc lại sau 6h
⏸ #3 Đi bộ    — `30 6 * * 0,6`
```

- ✅ = đang bật, ⏸ = đang tắt
- Số `#N` là ID dùng cho lệnh `/pause`, `/resume`, `/nudge`, `/removetask`

## 5. Tắt / bật từng nhắc nhở

```
/pause 2        — chỉ tắt nhắc #2
/resume 2       — bật lại nhắc #2
/pause          — tắt TẤT CẢ nhắc nhở
/resume         — bật lại TẤT CẢ
```

## 6. Đặt giờ nhắc lại (`/nudge`)

Khi bạn không trả lời check-in, bot sẽ nhắc lại sau N giờ (mặc định 12h). Đổi cho từng nhắc nhở:

```
/nudge 3 6      — nhắc lại sau 6 giờ
/nudge 3 0      — TẮT nhắc lại (chỉ ping 1 lần)
```

## 7. Xóa nhắc nhở

```
/removetask 2
```

## 8. Đổi múi giờ bất cứ lúc nào

```
/tz             — xem múi giờ hiện tại
/tz Tokyo       — đổi sang Tokyo
/tz +9          — đổi sang UTC+9
/tz Singapore   — đổi sang Singapore
```

## 9. Trò chuyện với AI

Cứ nhắn bất cứ điều gì — bot sẽ cố gắng trả lời với sự đồng cảm:

> *Bạn:* hôm nay mình mệt quá, không muốn làm gì
> *Bot:* Mình hiểu cảm giác đó. Có khi cơ thể đang cần nghỉ ngơi thật sự. Bạn thử nằm 10 phút mắt nhắm, hít thở sâu, không cố làm gì cả — đôi khi đó là thứ cần nhất 💙

Sau câu trả lời, bot sẽ hiển thị 2 nút:
- 👍 **Có ích** — bot sẽ nhớ và học từ câu trả lời này
- 👎 **Chưa giúp được** — bot sẽ thử cách khác

Nếu bạn 👎 nhiều lần liên tiếp (10 lần), bot sẽ chuyển bạn sang **coach con người**.

## 10. Kết nối coach con người (`/talk_to_human`)

Khi cần nói chuyện với người thật:
```
/talk_to_human
```

Coach sẽ liên hệ với bạn sớm. Trong khi chờ, bạn vẫn dùng được bot bình thường.

## 11. Khi đang trong tình huống khẩn cấp

Nếu bạn gõ những từ như "tự tử", "muốn chết", "hurt myself", bot sẽ tạm dừng AI và gửi ngay thông tin đường dây nóng:

- **Việt Nam (24/7, miễn phí):** `1800 599 920`
- **Quốc tế:** https://findahelpline.com

Bạn không đơn độc. 💙

## 12. Danh sách lệnh đầy đủ

| Lệnh | Việc làm |
|---|---|
| `/start` | Đăng ký lần đầu |
| `/help` | Xem danh sách lệnh |
| `/tz [thành phố]` | Xem / đổi múi giờ |
| `/tasks` | Liệt kê nhắc nhở |
| `/addtask <tên> \| <giờ>` | Thêm nhắc nhở |
| `/removetask <id>` | Xóa nhắc nhở |
| `/pause [id]` | Tắt 1 nhắc (hoặc tất cả) |
| `/resume [id]` | Bật lại 1 nhắc (hoặc tất cả) |
| `/nudge <id> <giờ>` | Đặt giờ nhắc lại |
| `/talk_to_human` | Kết nối coach con người |

## 13. Câu hỏi thường gặp

**Q: Bot có lưu nội dung tôi nhắn không?**
A: Có — bot lưu để cải thiện chất lượng trả lời và để coach có ngữ cảnh khi cần. Coach con người **không thấy** nội dung cụ thể trừ khi bạn `/talk_to_human` hoặc bot escalate.

**Q: Tôi gõ tên thành phố mà bot không nhận?**
A: Thử bằng tiếng Anh (`Hanoi`, `Tokyo`, `Saigon`) hoặc múi giờ trực tiếp (`+7`, `UTC+9`). Nếu vẫn không được, dùng tên IANA đầy đủ (`Asia/Ho_Chi_Minh`).

**Q: Tôi muốn tạm dừng tất cả nhắc nhở khi đi du lịch?**
A: Gõ `/pause` (không kèm số). Khi về lại, gõ `/resume`.

**Q: Bot không trả lời tin nhắn của tôi?**
A: Có thể bạn chưa hoàn thành bước cài múi giờ. Gõ `/start` để bắt đầu lại.

**Q: Tôi không phải người được mời nhưng vẫn tìm thấy bot?**
A: Bot này có thể đang ở chế độ riêng tư. Liên hệ admin và gửi `user_id` của bạn để được thêm vào danh sách.

---

Có thắc mắc khác? Cứ nhắn cho bot — nó sẽ cố giúp, hoặc dùng `/talk_to_human` để gặp người thật. 💚
