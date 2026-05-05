"""Gemini Flash client for grounded RAG.

The LLM is *only* used as a fallback when the KB matcher score is below
the satisfaction threshold. The prompt is built so the model is constrained
to the KB context and instructed to acknowledge limits — no hallucinated
medical / clinical advice.
"""

from __future__ import annotations

import logging
from typing import List, Tuple

import google.generativeai as genai

from config import settings
from services.kb import KBEntry

log = logging.getLogger(__name__)

_model = None
_initialized = False


def _ensure_initialized() -> None:
    global _model, _initialized
    if _initialized:
        return
    s = settings()
    genai.configure(api_key=s.gemini_api_key)
    _model = genai.GenerativeModel(
        s.gemini_model,
        generation_config={
            "temperature": 0.6,
            "top_p": 0.9,
            "max_output_tokens": 400,
        },
        safety_settings=[
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        ],
    )
    _initialized = True


SYSTEM_PROMPT = """You are "Soul Coach", a warm, calm mental-coaching assistant.

RULES:
- Use ONLY the KB CONTEXT below as the source of techniques. You may rephrase \
or shorten, but do not invent new techniques outside what's grounded there.
- If the KB CONTEXT doesn't cover the user's question, give a brief, hedged, \
empathetic reply (2–3 sentences) that acknowledges the limit and suggests \
the user describe their situation in more detail or wait for a human coach.
- Never claim to be a therapist or doctor. For crisis topics (self-harm, \
suicide, abuse), gently encourage reaching out to a professional or a trusted \
person, and do not give clinical instructions.
- Reply in the same language as the user (English or Vietnamese).
- Keep replies under 120 words. No bullet lists unless the KB entry uses them.
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
    """history is list of (role, text) where role in {U, B}."""
    if not history:
        return "(no prior turns)"
    return "\n".join(f"{role}: {text}" for role, text in history)


def soft_reply(query: str,
               kb_candidates: List[Tuple[KBEntry, float]],
               history: List[Tuple[str, str]]) -> str:
    """Generate a grounded soft-reply. Returns the bot's reply text."""
    _ensure_initialized()
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"KB CONTEXT:\n{_format_kb(kb_candidates)}\n\n"
        f"CONVERSATION (last turns):\n{_format_history(history)}\n\n"
        f"USER MESSAGE:\n{query}\n\n"
        "Reply now as Soul Coach:"
    )
    try:
        resp = _model.generate_content(prompt)
        text = (resp.text or "").strip()
        if not text:
            raise RuntimeError("empty response")
        return text
    except Exception as e:
        log.exception("Gemini call failed: %s", e)
        return (
            "I want to give you a thoughtful answer but I'm having trouble "
            "right now. Let me bring in a human coach — they'll reach out shortly."
        )
