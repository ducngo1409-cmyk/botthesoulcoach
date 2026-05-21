"""User registration and basic command handlers.

State machine:

    NOT_REGISTERED  ── /start ──▶  AWAITING_TZ ──┐
                                                  │ valid tz / 'skip' / /tz <city>
                                                  ▼
                                              ONBOARDED ──▶ free use of all features

The state is **persisted in `users.onboarded`** (0=awaiting, 1=done) so it
survives bot restarts. The previous in-memory `_awaiting_tz` set was the
root cause of users getting stuck — if the bot restarted while a user was
mid-onboarding, the state was lost and the user's next message was treated
as a regular chat input.

`handlers/access.gate` enforces the state: while `onboarded=0`, only
`/start`, `/talk_to_human` and the actual tz-reply handler run. Everything
else is replied with a gentle reminder.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import settings
from db import conn, transaction
from services.tz_aliases import resolve_tz

log = logging.getLogger(__name__)


# --- State queries (DB-backed) ------------------------------------------

def is_awaiting_tz(user_id: int) -> bool:
    """True iff user is registered but has not finished tz onboarding."""
    row = conn().execute(
        "SELECT onboarded FROM users WHERE tg_id = ?", (user_id,)
    ).fetchone()
    return bool(row) and not row["onboarded"]


def _mark_onboarded(user_id: int) -> None:
    with transaction() as cx:
        cx.execute("UPDATE users SET onboarded = 1 WHERE tg_id = ?", (user_id,))


# --- /start --------------------------------------------------------------

def _register_user(tg_id: int, name: str | None) -> tuple[bool, str]:
    """Insert user. Returns (fresh, access_status).

    Supervisor is auto-approved. Everyone else starts pending (unless
    REQUIRE_APPROVAL=0, in which case they're auto-approved).
    """
    s = settings()
    is_supervisor = (tg_id == s.supervisor_chat_id)
    access_status = "approved" if (is_supervisor or not s.require_approval) else "pending"

    with transaction() as cx:
        cur = cx.execute(
            "INSERT OR IGNORE INTO users (tg_id, name, tz, onboarded, access_status) "
            "VALUES (?, ?, ?, 0, ?)",
            (tg_id, name or "", s.default_tz, access_status),
        )
        fresh = cur.rowcount > 0

    row = conn().execute(
        "SELECT access_status FROM users WHERE tg_id = ?", (tg_id,)
    ).fetchone()
    return fresh, row["access_status"]


def get_access_status(user_id: int) -> str | None:
    row = conn().execute(
        "SELECT access_status FROM users WHERE tg_id = ?", (user_id,)
    ).fetchone()
    return row["access_status"] if row else None


async def _notify_admin_pending(context: ContextTypes.DEFAULT_TYPE, user) -> None:
    """DM every admin with approve/reject buttons for a new pending user."""
    from services import roles
    name = user.full_name or "?"
    username = f"@{user.username}" if user.username else "(no username)"
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Duyệt", callback_data=f"usr_app:{user.id}"),
        InlineKeyboardButton("❌ Từ chối", callback_data=f"usr_rej:{user.id}"),
    ]])
    text = (
        f"🆕 *Yêu cầu truy cập mới*\n\n"
        f"👤 {name}\n"
        f"🆔 `{user.id}`\n"
        f"📱 {username}\n\n"
        f"Hoặc dùng `/approve {user.id}` / `/reject {user.id}`."
    )
    for admin_id in roles.get_ids_with_perm("manage_users"):
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        except Exception:
            log.warning("Failed to notify admin %s about pending user %s", admin_id, user.id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    fresh, access = _register_user(user.id, user.full_name)

    # Path 1: New user, pending approval
    if fresh and access == "pending":
        await update.message.reply_text(
            f"👋 Xin chào {user.first_name}, mình là *Soul Coach*.\n\n"
            "🔒 Yêu cầu của bạn đã được gửi đến admin để duyệt.\n"
            "Bạn sẽ nhận được tin nhắn ngay khi được chấp nhận. Cảm ơn bạn đã kiên nhẫn!",
            parse_mode="Markdown",
        )
        await _notify_admin_pending(context, user)
        log.info("New pending user registered: %s (%s)", user.id, user.full_name)
        return

    # Path 2: New user, auto-approved (supervisor or REQUIRE_APPROVAL=0)
    if fresh and access == "approved":
        await update.message.reply_text(
            f"👋 Xin chào {user.first_name}, mình là *Soul Coach* của bạn.\n\n"
            "Mình giúp bạn duy trì những thói quen tốt và lắng nghe khi cần.",
            parse_mode="Markdown",
        )
        await _send_tz_prompt(update)
        return

    # Path 3: Existing user
    if access == "pending":
        await update.message.reply_text(
            "⏳ Yêu cầu truy cập của bạn vẫn đang chờ admin duyệt. "
            "Mình sẽ nhắn lại ngay khi được chấp nhận. 🙏"
        )
        return
    if access == "rejected":
        await update.message.reply_text(
            "🚫 Rất tiếc, yêu cầu truy cập của bạn chưa được chấp nhận."
        )
        return
    # access == "approved"
    if is_awaiting_tz(user.id):
        await _send_tz_prompt(update)
    else:
        await update.message.reply_text(
            f"👋 Chào mừng trở lại, {user.first_name}! Nhắn /help để xem mình có thể làm gì."
        )


async def _send_tz_prompt(update: Update) -> None:
    await update.message.reply_text(
        "🕐 *Trước tiên, bạn đang ở thành phố nào?*\n\n"
        "_Ví dụ:_ `Hanoi`, `Saigon`, `Tokyo`, `Singapore`, `+7`, `UTC+9`\n"
        "_Hoặc nhắn `skip` để giữ mặc định Asia/Ho_Chi_Minh._\n\n"
        "⚠️ Mình cần thông tin này trước khi giúp bạn được — "
        "tất cả nhắc nhở sẽ dùng giờ địa phương của bạn.",
        parse_mode="Markdown",
    )


# --- TZ reply handler ----------------------------------------------------

async def handle_tz_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Process a tz-setup reply. Called by access.gate while user is onboarding.

    Returns True iff the message was consumed.
    """
    user = update.effective_user
    if user is None or not is_awaiting_tz(user.id):
        return False

    text = (update.message.text or "").strip()

    # Explicit skip only — common words like 'không' are NOT in this set.
    if text.lower() in {"skip", "bỏ qua", "bo qua", "/skip"}:
        _mark_onboarded(user.id)
        await update.message.reply_text(
            "👌 Giữ múi giờ mặc định *Asia/Ho_Chi_Minh*.\n"
            "Đổi bất cứ lúc nào bằng `/tz <thành phố>`.\n\n"
            "Giờ thì bạn có thể dùng đầy đủ tính năng — gõ `/help` để bắt đầu.",
            parse_mode="Markdown",
        )
        log.info("User %s skipped tz onboarding", user.id)
        return True

    iana = resolve_tz(text)
    if iana:
        with transaction() as cx:
            cx.execute(
                "UPDATE users SET tz = ?, onboarded = 1 WHERE tg_id = ?",
                (iana, user.id),
            )
        await update.message.reply_text(
            f"✅ Đã đặt múi giờ *{iana}*.\n\n"
            "Giờ thì bạn dùng đầy đủ được rồi — gõ `/help` để bắt đầu, "
            "hoặc thử ngay: `/addtask Thiền | daily 7:00`",
            parse_mode="Markdown",
        )
        log.info("User %s onboarded with tz %r → %s", user.id, text, iana)
        return True

    # Unrecognized input — STAY in awaiting state and retry
    await update.message.reply_text(
        f"⚠️ Mình chưa nhận ra *{text[:40]}*. Thử lại với:\n"
        "• Tên thành phố: `Hanoi`, `Tokyo`, `Singapore`, `London`\n"
        "• Quốc gia: `Vietnam`, `Japan`, `UK`\n"
        "• Múi giờ: `+7`, `UTC+9`, `GMT-5`\n"
        "• Hoặc `skip` để giữ mặc định",
        parse_mode="Markdown",
    )
    log.debug("User %s sent unrecognized tz %r (still awaiting)", user.id, text)
    return True


# --- /tz command ---------------------------------------------------------

async def tz_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/tz <city|country|offset> — set or change timezone any time.

    Also valid during onboarding: works as an alternative way to set tz.
    """
    user = update.effective_user
    if user is None:
        return

    if not context.args:
        row = conn().execute("SELECT tz FROM users WHERE tg_id = ?", (user.id,)).fetchone()
        current = row["tz"] if row else "?"
        await update.message.reply_text(
            f"🕐 Múi giờ hiện tại: *{current}*\n"
            "Đổi bằng `/tz <thành phố hoặc múi giờ>`\n"
            "Vd: `/tz Hanoi`, `/tz Tokyo`, `/tz +7`",
            parse_mode="Markdown",
        )
        return

    arg = " ".join(context.args).strip()
    iana = resolve_tz(arg)
    if iana:
        with transaction() as cx:
            cx.execute(
                "UPDATE users SET tz = ?, onboarded = 1 WHERE tg_id = ?",
                (iana, user.id),
            )
        await update.message.reply_text(
            f"✅ Đã đặt múi giờ *{iana}*.", parse_mode="Markdown"
        )
        log.info("User %s changed tz to %s via /tz", user.id, iana)
    else:
        await update.message.reply_text(
            f"⚠️ Không nhận ra *{arg[:40]}*. Thử `Hanoi`, `Tokyo`, hay `+7`.",
            parse_mode="Markdown",
        )


# --- /help ---------------------------------------------------------------

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from services import roles
    user = update.effective_user
    role = roles.get_role(user.id) if user else "user"

    parts = []

    # Always show user-level commands
    parts.append(
        "📋 *Lệnh cho mọi người*\n"
        "/start — đăng ký\n"
        "/myrole — xem role của bạn\n"
        "/tz \\[thành phố] — xem/đổi múi giờ (vd `/tz Tokyo`)\n"
        "/tasks — xem nhắc nhở\n"
        "/addtask <tên> | <giờ> — thêm nhắc nhở\n"
        "  vd: `/addtask Thiền | daily 7:00`\n"
        "  vd: `/addtask Họp | every monday 9:00`\n"
        "  vd: `/addtask Báo cáo | weekdays 17:30`\n"
        "/removetask <id> — xóa nhắc nhở\n"
        "/pause \\[id] — tắt 1 nhắc (hoặc tất cả nếu không có id)\n"
        "/resume \\[id] — bật lại\n"
        "/nudge <id> <hours> — set giờ nhắc lại (0 = tắt nhắc lại)\n"
        "/talk\\_to\\_human — kết nối với coach con người\n\n"
        "_Hoặc cứ nhắn bất cứ điều gì, mình sẽ cố giúp._"
    )

    # Coacher-level commands (admin + coacher)
    if roles.has_perm(user.id, "view_users") if user else False:
        parts.append(
            "\n\n🎓 *Lệnh Coacher* (admin + coacher + service xem)\n"
            "/users \\[filter] — list user (filter: pending/approved/rejected/active/paused/blocked)\n"
            "/user <id> — profile + stats\n"
            "/user\\_tasks <id> — task của user\n"
            "/pending — user đang chờ duyệt\n"
            "/roles — danh sách staff"
        )
    if roles.has_perm(user.id, "handle_escalation") if user else False:
        parts.append(
            "\n\n💬 *Hỗ trợ user* (admin + coacher)\n"
            "/transcript <id> \\[YYYY-WW] — xem lịch sử hội thoại\n"
            "/resolve <id> — đóng escalation\n"
            "/dm <id> <msg> — gửi DM cho user\n"
            "/settask <id> | <tên> | <giờ> — giao nhắc nhở"
        )
    if roles.has_perm(user.id, "manage_kb") if user else False:
        parts.append(
            "\n\n📚 *Quản lý KB* (admin + coacher)\n"
            "/kb\\_add <cat> | <q> | <a> | <kw>\n"
            "/kb\\_list \\[cat]   /kb\\_edit <id> <field>=<value>   /kb\\_del <id>\n"
            "/kb\\_pending   /kb\\_approve <id> \\[cat] \\[kw]   /kb\\_reject <id>\n"
            "/kb\\_promote <interaction\\_id>"
        )
    if roles.has_perm(user.id, "view_reports") if user else False:
        parts.append(
            "\n\n📊 *Báo cáo*\n"
            "/report — gửi báo cáo tuần ngay"
        )
    if roles.has_perm(user.id, "view_debug") if user else False:
        parts.append(
            "\n\n🔧 *System* (admin + service)\n"
            "/debug — snapshot bot, escalations, lỗi gần nhất"
        )

    # Admin-only
    if roles.has_perm(user.id, "manage_users") if user else False:
        parts.append(
            "\n\n👑 *Quản lý user* (admin only)\n"
            "/approve <id> — duyệt user\n"
            "/reject <id> — từ chối user\n"
            "/revoke <id> — thu hồi quyền\n"
            "/block <id> /unblock <id> — block/gỡ block\n"
            "/freeze <id> /unfreeze <id> — pause/resume reminder\n"
            "/reonboard <id> — buộc set tz lại\n"
            "/delete\\_user <id> confirm — xóa hẳn"
        )
    if roles.has_perm(user.id, "manage_roles") if user else False:
        parts.append(
            "\n\n🛡 *Quản lý role* (admin only)\n"
            "/promote <id> <role> — gán role (admin/coacher/service/user)\n"
            "/demote <id> — về user"
        )
    if roles.has_perm(user.id, "broadcast") if user else False:
        parts.append(
            "\n\n📢 *Broadcast* (admin only)\n"
            "/broadcast <msg> — gửi cho tất cả approved+active"
        )

    parts.append(f"\n\n_Role hiện tại: {roles.ROLE_EMOJI.get(role, '?')} `{role}`_")
    await update.message.reply_text("".join(parts), parse_mode="Markdown")
