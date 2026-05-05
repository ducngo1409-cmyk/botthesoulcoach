"""Satisfaction tracking — buttons + free-text inference.

The counter increments on negative signals and resets on positive ones.
Used by the Q&A handler to decide when to escalate (counter >= threshold).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Literal

from db import conn, transaction

log = logging.getLogger(__name__)

Sentiment = Literal["positive", "negative", "neutral"]

# EN + VI keyword/regex lists. Word boundaries to avoid false positives
# (e.g. "no" matching "now").
_POSITIVE = re.compile(
    r"\b(thanks?|thank you|got it|that helps?|helped|that works?|"
    r"perfect|great|better now|makes sense|understood|"
    r"cảm ơn|tốt rồi|ổn rồi|hiểu rồi|đỡ rồi|được rồi)\b",
    re.IGNORECASE,
)

_NEGATIVE = re.compile(
    r"\b(still stuck|not really|doesn'?t help|didn'?t (work|help)|"
    r"tried that|nope|no help|useless|same problem|not (helpful|working)|"
    r"chưa được|không giúp|vẫn vậy|vẫn (chưa|không)|không ổn)\b",
    re.IGNORECASE,
)


def classify(text: str) -> Sentiment:
    """Quick rule-based sentiment for satisfaction tracking only."""
    if not text:
        return "neutral"
    if _POSITIVE.search(text):
        return "positive"
    if _NEGATIVE.search(text):
        return "negative"
    return "neutral"


# --- Session state -------------------------------------------------------

def _ensure_session(user_id: int) -> None:
    with transaction() as cx:
        cx.execute(
            "INSERT OR IGNORE INTO sessions (user_id, sat_counter) VALUES (?, 0)",
            (user_id,),
        )


def get_counter(user_id: int) -> int:
    _ensure_session(user_id)
    row = conn().execute(
        "SELECT sat_counter FROM sessions WHERE user_id = ?", (user_id,)
    ).fetchone()
    return int(row["sat_counter"]) if row else 0


def increment(user_id: int) -> int:
    """Increment counter, return new value."""
    _ensure_session(user_id)
    with transaction() as cx:
        cx.execute(
            "UPDATE sessions SET sat_counter = sat_counter + 1, "
            "last_unsat_at = datetime('now') WHERE user_id = ?",
            (user_id,),
        )
    return get_counter(user_id)


def reset(user_id: int, reason: str = "") -> None:
    _ensure_session(user_id)
    with transaction() as cx:
        cx.execute(
            "UPDATE sessions SET sat_counter = 0, last_unsat_at = NULL "
            "WHERE user_id = ?",
            (user_id,),
        )
    log.debug("Reset satisfaction counter for %s (reason=%s)", user_id, reason)


def is_escalated(user_id: int) -> bool:
    row = conn().execute(
        "SELECT escalated_at FROM sessions WHERE user_id = ?", (user_id,)
    ).fetchone()
    return bool(row and row["escalated_at"])


def mark_escalated(user_id: int) -> None:
    _ensure_session(user_id)
    with transaction() as cx:
        cx.execute(
            "UPDATE sessions SET escalated_at = datetime('now') WHERE user_id = ?",
            (user_id,),
        )


def clear_escalation(user_id: int) -> None:
    _ensure_session(user_id)
    with transaction() as cx:
        cx.execute(
            "UPDATE sessions SET escalated_at = NULL, sat_counter = 0 "
            "WHERE user_id = ?",
            (user_id,),
        )
