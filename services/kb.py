"""Knowledge-base CRUD + retrieval.

Retrieval strategy (v1): rapidfuzz `token_set_ratio` over a concatenation of
question + keywords. Top-K with a numeric score (0..100). The configured
`fuzzy_threshold` decides direct-answer vs. RAG fallback.

We deliberately *avoid* `WRatio` here. WRatio is biased toward partial
matches on common short tokens ("how do I", "what is"), which causes
unrelated user questions to score 85+ against unrelated KB entries.
`token_set_ratio` scores genuine matches in the 80s/90s while dropping
unrelated queries to the 30s/50s — exactly the discrimination we want.

KB writes invalidate the in-memory cache so newly added entries are
available on the very next user message.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import List, Optional, Tuple

from rapidfuzz import fuzz, process

from db import conn, transaction

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class KBEntry:
    id: int
    category: str
    question: str
    answer: str
    keywords: str
    hits: int = 0


# --- Cache ---------------------------------------------------------------

_cache_lock = threading.RLock()
_cache: List[KBEntry] | None = None


def _load_cache() -> List[KBEntry]:
    rows = conn().execute(
        "SELECT id, category, question, answer, keywords, hits "
        "FROM kb_entries ORDER BY id"
    ).fetchall()
    return [KBEntry(**dict(r)) for r in rows]


def _ensure_cache() -> List[KBEntry]:
    global _cache
    with _cache_lock:
        if _cache is None:
            _cache = _load_cache()
        return _cache


def invalidate_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None


# --- CRUD ----------------------------------------------------------------

def add(category: str, question: str, answer: str, keywords: str,
        created_by: Optional[int] = None) -> int:
    with transaction() as cx:
        cur = cx.execute(
            "INSERT INTO kb_entries (category, question, answer, keywords, created_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (category.strip(), question.strip(), answer.strip(),
             keywords.strip(), created_by),
        )
        new_id = cur.lastrowid
    invalidate_cache()
    log.info("KB add: id=%s category=%s by=%s", new_id, category, created_by)
    return new_id


def get(entry_id: int) -> Optional[KBEntry]:
    row = conn().execute(
        "SELECT id, category, question, answer, keywords, hits "
        "FROM kb_entries WHERE id = ?", (entry_id,),
    ).fetchone()
    return KBEntry(**dict(row)) if row else None


def list_all(category: Optional[str] = None) -> List[KBEntry]:
    if category:
        rows = conn().execute(
            "SELECT id, category, question, answer, keywords, hits "
            "FROM kb_entries WHERE category = ? ORDER BY id", (category,),
        ).fetchall()
    else:
        rows = conn().execute(
            "SELECT id, category, question, answer, keywords, hits "
            "FROM kb_entries ORDER BY category, id"
        ).fetchall()
    return [KBEntry(**dict(r)) for r in rows]


def edit(entry_id: int, **fields) -> bool:
    allowed = {"category", "question", "answer", "keywords"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return False
    sets = ", ".join(f"{k} = ?" for k in fields)
    with transaction() as cx:
        cur = cx.execute(
            f"UPDATE kb_entries SET {sets} WHERE id = ?",
            (*fields.values(), entry_id),
        )
        ok = cur.rowcount > 0
    invalidate_cache()
    return ok


def delete(entry_id: int) -> bool:
    with transaction() as cx:
        cur = cx.execute("DELETE FROM kb_entries WHERE id = ?", (entry_id,))
        ok = cur.rowcount > 0
    invalidate_cache()
    return ok


def increment_hits(entry_id: int) -> None:
    with transaction() as cx:
        cx.execute("UPDATE kb_entries SET hits = hits + 1 WHERE id = ?", (entry_id,))
    # don't invalidate cache for a hit counter — stale OK


# --- Retrieval -----------------------------------------------------------

def _haystack(e: KBEntry) -> str:
    return f"{e.question}  {e.keywords}"


def search(query: str, top_k: int = 5) -> List[Tuple[KBEntry, float]]:
    """Return top-K (entry, score) pairs. Score is rapidfuzz 0..100."""
    if not query.strip():
        return []
    entries = _ensure_cache()
    if not entries:
        return []

    haystack = {i: _haystack(e) for i, e in enumerate(entries)}
    matches = process.extract(
        query, haystack, scorer=fuzz.token_set_ratio, limit=top_k
    )
    # process.extract returns (matched_str, score, key)
    return [(entries[key], float(score)) for _, score, key in matches]


def best_match(query: str) -> Optional[Tuple[KBEntry, float]]:
    res = search(query, top_k=1)
    return res[0] if res else None
