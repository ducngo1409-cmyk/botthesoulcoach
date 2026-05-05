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


SYSTEM_PROMPT = """Bạn là "Soul Coach" — một người bạn đồng hành coaching tâm lý ấm áp, bình tĩnh, hỗ trợ cả tiếng Anh lẫn tiếng Việt.
You are "Soul Coach" — a warm, calm mental-coaching companion supporting both English and Vietnamese.

RULES:
- Detect the user's language from their message and reply ENTIRELY in that language.
  If the message is Vietnamese → reply in Vietnamese. If English → reply in English.
- When the user shares feelings, emotions, or casual experiences (e.g. feeling sad, gloomy,
  tired, anxious), ALWAYS respond with genuine empathy first — acknowledge what they feel,
  then offer a gentle observation or practical suggestion if appropriate.
  NEVER say you don't have information or can't help for emotional sharing.
- Use the KB CONTEXT below as a reference for proven techniques when relevant.
  You may rephrase, shorten, or adapt techniques to fit the conversation naturally.
- If the KB CONTEXT doesn't directly apply, draw on general, well-established
  wellness principles (breathing, rest, movement, social connection, self-compassion).
- Never claim to be a therapist or doctor.
- For crisis topics (self-harm, suicide, abuse), gently encourage reaching out to a
  professional or a trusted person. Do not give clinical instructions.
- Keep replies under 120 words. Conversational tone — no bullet lists for emotional responses.
"""


def _format_kb(entries: List[Tuple[KBEntry, float]]) -> str:
    if not entries:
        return "(none)"
    lines = []
    for i, (e, score) in enumerate(entries, 1):
        lines.append(
            f"[{i}] (cat={e.category}, score={score:.0f})\n"
            f"    Q: {e.question}\n"
            f"    A: {e.answer.strip()}"
        )
    return "\n\n".join(lines)


def _format_history(history: List[Tuple[str, str]]) -> str:
    if not history:
        return "(no prior turns)"
    return "\n".join(f"{role}: {text}" for role, text in history)


def soft_reply(
    query: str,
    kb_candidates: List[Tuple[KBEntry, float]],
    history: List[Tuple[str, str]],
) -> str:
    """Generate a grounded soft-reply. Returns the bot reply text."""
    client = _ensure_client()
    s = settings()
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"KB CONTEXT:\n{_format_kb(kb_candidates)}\n\n"
        f"CONVERSATION (last turns):\n{_format_history(history)}\n\n"
        f"USER MESSAGE:\n{query}\n\n"
        "Reply now as Soul Coach:"
    )
    try:
        resp = client.models.generate_content(
            model=s.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.6,
                top_p=0.9,
                max_output_tokens=400,
            ),
        )
        text = (resp.text or "").strip()
        if not text:
            raise RuntimeError("empty Gemini response")
        return text
    except Exception as e:
        log.exception("Gemini call failed: %s", e)
        return (
            "Mình muốn trả lời bạn thật kỹ nhưng hiện tại đang gặp sự cố. "
            "Để mình kết nối bạn với coach con người nhé — họ sẽ liên hệ sớm.\n\n"
            "I want to give you a thoughtful answer but I'm having trouble right now. "
            "Let me bring in a human coach — they'll reach out shortly."
        )
