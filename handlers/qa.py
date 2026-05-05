"""Free-text Q&A flow.

Pipeline:
  1. Log incoming message.
  2. If user is awaiting timezone reply → delegate to onboarding handler.
  3. Crisis-keyword pre-filter → immediate safe-messaging response, no LLM.
  4. If user is currently escalated → silent (S handles them directly).
  5. Run satisfaction classifier on the text.
       - 'positive' → reset counter (with a short ack).
       - 'negative' → increment counter; if >= threshold, escalate.
  6. KB retrieve top-5.
  7. If top1 >= fuzzy_threshold → answer from KB + 👍/👎 buttons.
  8. Else → Gemini RAG soft-reply + 👍/👎 buttons. Mark llm=1.
"""

from __future__ import annotations

import logging
from typing import List, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import settings
from db import conn, transaction
from services import kb, satisfaction, llm
from services.kb import KBEntry

log = logging.getLogger(__name__)

FEEDBACK_PREFIX_LLM = "💡 Gợi ý từ Soul Coach:\n\n"

# Crisis keywords (EN + VI). Matched case-insensitively via substring search.
_CRISIS_KEYWORDS = [
    # English
    "kill myself", "end my life", "suicide", "suicidal",
    "self-harm", "self harm", "hurt myself", "cut myself",
    "want to die", "don't want to live", "dont want to live",
    "no reason to live", "not worth living",
    # Vietnamese
    "tự tử", "muốn chết", "tự làm đau", "không muốn sống",
    "không có lý do sống", "chán sống",
]

_CRISIS_REPLY = (
    "💙 Mình nghe thấy bạn đang trải qua điều rất đau lòng.\n"
    "Hãy liên hệ với chuyên gia hoặc người bạn tin tưởng — bạn không phải đối mặt một mình.\n\n"
    "💙 I can hear that you're going through something really painful.\n"
    "Please reach out to a professional or someone you trust — you don't have to face this alone.\n\n"
    "🆘 Hỗ trợ khẩn cấp / Crisis support:\n"
    "• Việt Nam (miễn phí, 24/7): 1800 599 920\n"
    "• Quốc tế / International: findahelpline.com\n\n"
    "Mình luôn ở đây, nhưng mình không thể thay thế sự hỗ trợ từ con người thật. Bạn quan trọng lắm. 💙"
)


def _is_crisis(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _CRISIS_KEYWORDS)


# --- Helpers -------------------------------------------------------------

def _log_in(user_id: int, text: str) -> int:
    with transaction() as cx:
        cur = cx.execute(
            "INSERT INTO interactions (user_id, direction, text, intent) "
            "VALUES (?, 'in', ?, 'qa')",
            (user_id, text),
        )
        return cur.lastrowid


def _log_out(user_id: int, text: str, kb_match_id: int | None, llm_used: bool) -> int:
    with transaction() as cx:
        cur = cx.execute(
            "INSERT INTO interactions (user_id, direction, text, intent, kb_match_id, llm) "
            "VALUES (?, 'out', ?, 'qa', ?, ?)",
            (user_id, text, kb_match_id, 1 if llm_used else 0),
        )
        return cur.lastrowid


def _feedback_keyboard(interaction_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("👍 Có ích", callback_data=f"sat:{interaction_id}:+"),
        InlineKeyboardButton("👎 Chưa giúp được", callback_data=f"sat:{interaction_id}:-"),
    ]])


def _recent_history(user_id: int, n: int = 6) -> List[Tuple[str, str]]:
    rows = conn().execute(
        "SELECT direction, text FROM interactions "
        "WHERE user_id = ? AND intent = 'qa' "
        "ORDER BY id DESC LIMIT ?",
        (user_id, n),
    ).fetchall()
    rows.reverse()
    return [("U" if r["direction"] == "in" else "B", r["text"]) for r in rows]


# --- Main entry ----------------------------------------------------------

async def on_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    msg = update.message
    if user is None or msg is None or not msg.text:
        return
    text = msg.text.strip()
    s = settings()

    # Timezone onboarding reply — intercept before any other logic.
    from handlers.onboarding import handle_tz_reply
    if await handle_tz_reply(update, context):
        return

    _log_in(user.id, text)

    # Crisis-keyword pre-filter — safe-messaging response, skip LLM entirely.
    if _is_crisis(text):
        log.info("Crisis keyword detected for user %s — sending safe-messaging reply", user.id)
        await msg.reply_text(_CRISIS_REPLY, parse_mode="Markdown")
        return

    # If user is escalated, stay quiet — S is handling.
    if satisfaction.is_escalated(user.id):
        log.debug("User %s is escalated; bot stays silent", user.id)
        return

    # Apply satisfaction signal from the text itself
    sentiment = satisfaction.classify(text)
    if sentiment == "positive":
        satisfaction.reset(user.id, "user expressed positive sentiment")
    elif sentiment == "negative":
        new_counter = satisfaction.increment(user.id)
        if new_counter >= s.sat_threshold:
            from handlers.escalation import escalate
            await escalate(context, user.id, reason="counter")
            return

    # KB retrieval
    candidates = kb.search(text, top_k=5)
    top = candidates[0] if candidates else None

    if top and top[1] >= s.fuzzy_threshold:
        # Direct KB answer
        entry, score = top
        kb.increment_hits(entry.id)
        reply = entry.answer
        interaction_id = _log_out(user.id, reply, kb_match_id=entry.id, llm_used=False)
        await msg.reply_text(
            reply,
            reply_markup=_feedback_keyboard(interaction_id),
        )
        return

    # KB miss → Gemini RAG soft reply (no parse_mode — LLM text may have unbalanced markdown)
    history = _recent_history(user.id, n=2)
    soft = llm.soft_reply(text, candidates, history)
    reply = FEEDBACK_PREFIX_LLM + soft
    interaction_id = _log_out(user.id, soft, kb_match_id=None, llm_used=True)
    await msg.reply_text(
        reply,
        reply_markup=_feedback_keyboard(interaction_id),
    )


# --- Auto KB promotion ---------------------------------------------------

def _promote_to_kb(bot_reply: str, user_id: int, interaction_id: int) -> tuple[int, str]:
    """Insert KB entry, update interaction. Returns (new_kb_id, question)."""
    user_msg = conn().execute(
        "SELECT text FROM interactions "
        "WHERE user_id = ? AND id < ? AND direction = 'in' "
        "ORDER BY id DESC LIMIT 1",
        (user_id, interaction_id),
    ).fetchone()
    question = user_msg["text"] if user_msg else "(unknown)"

    new_id = kb.add(
        category="general",
        question=question,
        answer=bot_reply,
        keywords="",
        created_by=None,
    )
    with transaction() as cx:
        cx.execute(
            "UPDATE interactions SET kb_match_id = ? WHERE id = ?",
            (new_id, interaction_id),
        )
    log.info("Auto-promoted interaction %s to KB #%s", interaction_id, new_id)
    return new_id, question


# --- Feedback button callback -------------------------------------------

async def feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        _, interaction_id, sign = query.data.split(":")
        interaction_id = int(interaction_id)
    except Exception:
        return

    user_id = update.effective_user.id

    row = conn().execute(
        "SELECT llm, text FROM interactions WHERE id = ?", (interaction_id,)
    ).fetchone()
    is_llm = bool(row and row["llm"])

    if sign == "+":
        with transaction() as cx:
            cx.execute(
                "UPDATE interactions SET satisfied = 1 WHERE id = ?",
                (interaction_id,),
            )
        satisfaction.reset(user_id, "thumbs up")
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(
            chat_id=user_id, text="🌟 Vui vì mình giúp được bạn!"
        )

        # Auto-promote LLM reply to KB and notify supervisor
        if is_llm and row:
            new_kb_id, question = _promote_to_kb(row["text"], user_id, interaction_id)
            s = settings()
            try:
                await context.bot.send_message(
                    chat_id=s.supervisor_chat_id,
                    text=(
                        f"📚 Bot vừa học câu trả lời mới (KB #{new_kb_id}).\n"
                        f"Câu hỏi: {question[:120]}\n\n"
                        f"/kb_edit {new_kb_id} category=<cat> để phân loại\n"
                        f"/kb_edit {new_kb_id} keywords=<kw> để thêm từ khóa"
                    ),
                )
            except Exception:
                log.warning("Could not notify supervisor about KB #%s", new_kb_id)
        return

    # 👎
    with transaction() as cx:
        cx.execute(
            "UPDATE interactions SET satisfied = 0 WHERE id = ?",
            (interaction_id,),
        )

    new_counter = satisfaction.increment(user_id)
    s = settings()
    await query.edit_message_reply_markup(reply_markup=None)

    if new_counter >= s.sat_threshold:
        from handlers.escalation import escalate
        reason = "counter"
        await escalate(context, user_id, reason=reason)
        return

    await context.bot.send_message(
        chat_id=user_id,
        text="Mình hiểu rồi — để mình thử theo hướng khác nhé. "
             "Bạn có thể kể thêm một chút về tình huống của bạn không?",
    )
