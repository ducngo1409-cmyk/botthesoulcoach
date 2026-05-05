"""Gemini Flash client for grounded RAG.

Key design decisions:
- system_instruction passed via config (not concatenated into contents) — lets
  Google apply context caching on the stable instruction portion.
- Multi-key failover: rotates to the next key on 429 before giving up.
- Raises LLMQuotaError on exhausted quota so the caller can notify supervisor.
- Logs input/output token counts from usage_metadata after every call.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from google import genai
from google.genai import types

from config import settings
from services.kb import KBEntry

log = logging.getLogger(__name__)

# --- Exception types used by callers ------------------------------------

class LLMQuotaError(Exception):
    """Raised when all API keys are quota-exhausted (HTTP 429)."""

class LLMError(Exception):
    """Raised for any other Gemini API failure."""


# --- Client pool (one per API key) --------------------------------------

_clients: list[genai.Client] = []


def _init_clients() -> None:
    global _clients
    s = settings()
    keys = [s.gemini_api_key]
    if s.gemini_api_key_2:
        keys.append(s.gemini_api_key_2)
    _clients = [genai.Client(api_key=k) for k in keys]
    log.info("LLM: %d API key(s) configured", len(_clients))


def _get_clients() -> list[genai.Client]:
    if not _clients:
        _init_clients()
    return _clients


# --- Prompt building ----------------------------------------------------

# Compact system instruction (~60 tokens). Passed via config.system_instruction
# so Google can cache it separately from user content.
_SYSTEM = (
    "Soul Coach: ấm áp, ngắn gọn. Dùng ngôn ngữ của user (VI hoặc EN). "
    "Cảm xúc → đồng cảm trước, gợi ý nhẹ sau. Không bao giờ nói không biết. "
    "Dùng KB bên dưới nếu liên quan; nếu không dùng kiến thức wellness thông thường. "
    "Không tự nhận là bác sĩ. Tối đa 80 từ."
)

_KB_MIN_SCORE = 40
_KB_MAX_ENTRIES = 2


def _format_kb(entries: List[Tuple[KBEntry, float]]) -> str:
    relevant = [(e, s) for e, s in entries if s >= _KB_MIN_SCORE][:_KB_MAX_ENTRIES]
    if not relevant:
        return ""
    lines = []
    for e, _ in relevant:
        short_a = e.answer.strip().replace("\n", " ")[:100]
        lines.append(f"[{e.category}] {e.question} → {short_a}")
    return "\n".join(lines)


def _format_history(history: List[Tuple[str, str]]) -> str:
    if not history:
        return ""
    return "\n".join(f"{role}: {text}" for role, text in history)


def _build_prompt(
    query: str,
    kb_candidates: List[Tuple[KBEntry, float]],
    history: List[Tuple[str, str]],
) -> str:
    parts = []
    kb_block = _format_kb(kb_candidates)
    history_block = _format_history(history)
    if kb_block:
        parts.append(f"KB:\n{kb_block}")
    if history_block:
        parts.append(f"Lịch sử:\n{history_block}")
    parts.append(f"User: {query}\nSoul Coach:")
    return "\n\n".join(parts)


# --- Main call ----------------------------------------------------------

def soft_reply(
    query: str,
    kb_candidates: List[Tuple[KBEntry, float]],
    history: List[Tuple[str, str]],
) -> str:
    """Generate a grounded soft-reply.

    Tries each configured API key in order on quota errors.
    Raises LLMQuotaError if all keys are exhausted.
    Raises LLMError on other failures.
    """
    prompt = _build_prompt(query, kb_candidates, history)
    clients = _get_clients()
    s = settings()

    # Model failover list: primary first, then fallbacks with separate free-tier
    # buckets. Order matters — try the cheapest/most-available last.
    models = [m.strip() for m in s.gemini_model.split(",") if m.strip()]
    if not models:
        models = ["gemini-2.5-flash-lite"]

    last_exc: Optional[Exception] = None
    saw_quota = False
    for model in models:
        for idx, client in enumerate(clients):
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=_SYSTEM,
                        temperature=0.7,
                        top_p=0.9,
                        max_output_tokens=400,
                    ),
                )
                if resp.usage_metadata:
                    in_t = resp.usage_metadata.prompt_token_count or 0
                    out_t = resp.usage_metadata.candidates_token_count or 0
                    log.info(
                        "LLM tokens [%s key %d]: in=%d out=%d total=%d",
                        model, idx, in_t, out_t, in_t + out_t,
                    )
                text = (resp.text or "").strip()
                if text:
                    return text
                # Empty response (safety filter / model glitch) — failover, don't raise
                log.warning("LLM empty text [%s key %d] — trying next", model, idx)
                last_exc = LLMError(f"{model} key_{idx}: empty response")
                continue

            except Exception as e:
                code = getattr(e, "code", None)
                if code == 429:
                    saw_quota = True
                    log.warning("LLM 429 [%s key %d] — %s", model, idx, str(e)[:120])
                    last_exc = LLMQuotaError(f"{model} key_{idx}: {str(e)[:200]}")
                else:
                    # 5xx, network timeouts, parse errors, anything else: keep trying.
                    log.warning(
                        "LLM error [%s key %d] code=%s — %s",
                        model, idx, code, str(e)[:160],
                    )
                    last_exc = LLMError(f"{model} key_{idx}: {code} {str(e)[:200]}")
                continue

    # All models × keys exhausted. Raise quota error if any key 429'd (so the
    # supervisor gets the actionable quota DM); otherwise generic LLMError.
    if saw_quota:
        raise LLMQuotaError(str(last_exc) if last_exc else "all keys quota-exhausted")
    raise LLMError(str(last_exc) if last_exc else "all models/keys failed")
