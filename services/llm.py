"""Gemini Flash client for grounded RAG.

Migrated to the `google-genai` SDK (replaces deprecated `google-generativeai`).

The LLM is *only* used as a fallback when the KB matcher score is below the
fuzzy threshold. The prompt constrains the model to the KB context and
instructs it to reply in the user's language (EN or VI).
"""

from __future__ import annotations

import logging
from typing import List, Tuple

from google import genai
from google.genai import types

from config import settings
from services.kb import KBEntry

log = logging.getLogger(__name__)

_client: genai.Client | None = None


def _ensure_client() -> genai.Client:
    global _client
    if _client is None:
        s = settings()
        _client = genai.Client(api_key=s.gemini_api_key)
    return _client


# Compact system prompt — ~60 tokens vs ~350 before.
# The model infers context from KB entries; we don't need verbose instructions.
_SYSTEM = (
    "Soul Coach: ấm áp, ngắn gọn. Dùng ngôn ngữ của user (VI hoặc EN). "
    "Cảm xúc → đồng cảm trước, gợi ý nhẹ sau. Không bao giờ nói không biết. "
    "Dùng KB bên dưới nếu liên quan; nếu không dùng kiến thức wellness thông thường. "
    "Không tự nhận là bác sĩ. Tối đa 80 từ."
)

# KB relevance cutoff: don't send entries that scored below this — they just waste tokens.
_KB_MIN_SCORE = 40
# Max KB entries to include in prompt context
_KB_MAX_ENTRIES = 2


def _format_kb(entries: List[Tuple[KBEntry, float]]) -> str:
    """Only include relevant entries, with truncated answers to save tokens."""
    relevant = [(e, s) for e, s in entries if s >= _KB_MIN_SCORE][:_KB_MAX_ENTRIES]
    if not relevant:
        return ""
    lines = []
    for e, _ in relevant:
        # Truncate answer — model needs the gist, not the full text
        short_a = e.answer.strip().replace("\n", " ")[:100]
        lines.append(f"[{e.category}] {e.question} → {short_a}")
    return "\n".join(lines)


def _format_history(history: List[Tuple[str, str]]) -> str:
    if not history:
        return ""
    return "\n".join(f"{role}: {text}" for role, text in history)


def soft_reply(
    query: str,
    kb_candidates: List[Tuple[KBEntry, float]],
    history: List[Tuple[str, str]],
) -> str:
    """Generate a grounded soft-reply. Returns the bot reply text."""
    client = _ensure_client()
    s = settings()

    kb_block = _format_kb(kb_candidates)
    history_block = _format_history(history)

    # Build a minimal prompt — only include sections that have content
    parts = [_SYSTEM]
    if kb_block:
        parts.append(f"KB:\n{kb_block}")
    if history_block:
        parts.append(f"Lịch sử:\n{history_block}")
    parts.append(f"User: {query}\nSoul Coach:")

    prompt = "\n\n".join(parts)

    try:
        resp = client.models.generate_content(
            model=s.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.7,
                top_p=0.9,
                max_output_tokens=180,   # was 400 — 80-word cap needs ~120 tokens
            ),
        )
        text = (resp.text or "").strip()
        if not text:
            raise RuntimeError("empty Gemini response")
        return text
    except Exception as e:
        log.exception("Gemini call failed: %s", e)
        return (
            "Mình đang gặp chút sự cố kỹ thuật. "
            "Bạn có thể thử lại sau hoặc nhắn /talk_to_human để kết nối với coach nhé."
        )
