"""Weekly aggregate report for Supervisor S.

Cron-fires once a week. Aggregates per-user check-in compliance, mood trend,
interaction volume, escalations, and pending KB-promotion candidates.
Snippets are redacted by default; verbatim is available via /transcript.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
from datetime import datetime, timedelta, timezone

from telegram.ext import Application

from config import settings
from db import conn, transaction

log = logging.getLogger(__name__)


def _redact(text: str, n: int = 60) -> str:
    head = text[:n].replace("\n", " ")
    tail = "" if len(text) <= n else f"…[+{len(text) - n}ch]"
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return f"{head}{tail} `[{h}]`"


def _aggregate_payload(week_start_utc: datetime, week_end_utc: datetime) -> dict:
    cx = conn()
    fmt = "%Y-%m-%d %H:%M:%S"
    ws = week_start_utc.strftime(fmt)
    we = week_end_utc.strftime(fmt)

    users = cx.execute(
        "SELECT tg_id, name, status FROM users"
    ).fetchall()

    per_user = []
    for u in users:
        sent = cx.execute(
            "SELECT COUNT(*) AS n FROM check_ins "
            "WHERE user_id = ? AND sent_at BETWEEN ? AND ?",
            (u["tg_id"], ws, we),
        ).fetchone()["n"]
        answered = cx.execute(
            "SELECT COUNT(*) AS n FROM check_ins "
            "WHERE user_id = ? AND sent_at BETWEEN ? AND ? AND status = 'answered'",
            (u["tg_id"], ws, we),
        ).fetchone()["n"]
        mood_row = cx.execute(
            "SELECT AVG(mood) AS m FROM check_ins "
            "WHERE user_id = ? AND sent_at BETWEEN ? AND ? AND mood IS NOT NULL",
            (u["tg_id"], ws, we),
        ).fetchone()
        avg_mood = round(mood_row["m"], 2) if mood_row["m"] is not None else None
        interactions = cx.execute(
            "SELECT COUNT(*) AS n FROM interactions "
            "WHERE user_id = ? AND ts BETWEEN ? AND ?",
            (u["tg_id"], ws, we),
        ).fetchone()["n"]
        escalations = cx.execute(
            "SELECT reason, sent_to_s_at, resolved_at FROM escalations "
            "WHERE user_id = ? AND sent_to_s_at BETWEEN ? AND ?",
            (u["tg_id"], ws, we),
        ).fetchall()
        candidates = cx.execute(
            "SELECT id, text FROM interactions "
            "WHERE user_id = ? AND ts BETWEEN ? AND ? "
            "AND llm = 1 AND satisfied = 1",
            (u["tg_id"], ws, we),
        ).fetchall()

        per_user.append({
            "tg_id": u["tg_id"],
            "name": u["name"],
            "status": u["status"],
            "checkins_sent": sent,
            "checkins_answered": answered,
            "compliance_pct": (round(100 * answered / sent, 1) if sent else None),
            "avg_mood": avg_mood,
            "interactions": interactions,
            "escalations": [dict(e) for e in escalations],
            "kb_candidates": [
                {"id": c["id"], "redacted": _redact(c["text"])} for c in candidates
            ],
        })

    top_kb_hits = cx.execute(
        "SELECT k.id, k.category, k.question, COUNT(i.id) AS hits "
        "FROM kb_entries k JOIN interactions i ON i.kb_match_id = k.id "
        "WHERE i.ts BETWEEN ? AND ? "
        "GROUP BY k.id ORDER BY hits DESC LIMIT 5",
        (ws, we),
    ).fetchall()

    misses = cx.execute(
        "SELECT id, text FROM interactions "
        "WHERE direction = 'in' AND intent = 'qa' "
        "AND ts BETWEEN ? AND ? "
        "AND id IN ("
        "  SELECT i2.id FROM interactions i2 "
        "  LEFT JOIN interactions ans ON ans.id = i2.id + 1 "
        "  WHERE ans.llm = 1"
        ")",
        (ws, we),
    ).fetchall()

    return {
        "week_start": ws,
        "week_end": we,
        "per_user": per_user,
        "top_kb_hits": [dict(r) for r in top_kb_hits],
        "kb_miss_count": len(misses),
    }


def _format_markdown(payload: dict) -> str:
    lines = [
        f"📊 *Weekly Report* — {payload['week_start']} → {payload['week_end']}",
        "",
        "*Per-user summary:*",
    ]
    if not payload["per_user"]:
        lines.append("_(no users)_")
    for u in payload["per_user"]:
        comp = f"{u['compliance_pct']}%" if u["compliance_pct"] is not None else "—"
        mood = f"{u['avg_mood']}/5" if u["avg_mood"] is not None else "—"
        esc = len(u["escalations"])
        cand = len(u["kb_candidates"])
        lines.append(
            f"• `{u['tg_id']}` *{u['name'] or '?'}* "
            f"({u['status']}) — checkins {u['checkins_answered']}/{u['checkins_sent']} "
            f"({comp}), mood {mood}, msgs {u['interactions']}, "
            f"escalations {esc}, kb-candidates {cand}"
        )

    lines.append("")
    lines.append("*Top KB hits:*")
    if not payload["top_kb_hits"]:
        lines.append("_(none)_")
    for h in payload["top_kb_hits"]:
        lines.append(f"• #{h['id']} ({h['category']}) — {h['hits']} hits — {h['question']}")

    lines.append("")
    lines.append(f"*KB misses (LLM fallbacks):* {payload['kb_miss_count']}")
    lines.append("")
    lines.append(
        "_Run /transcript <user_id> to view verbatim. "
        "Run /kb_promote <interaction_id> to add a successful LLM reply to the KB._"
    )
    return "\n".join(lines)


async def send_weekly_report(app: Application) -> None:
    s = settings()
    now = datetime.now(timezone.utc)
    week_end = now
    week_start = now - timedelta(days=7)

    payload = _aggregate_payload(week_start, week_end)
    text = _format_markdown(payload)

    with transaction() as cx:
        cx.execute(
            "INSERT INTO reports (week_start, week_end, payload_json) VALUES (?, ?, ?)",
            (
                week_start.strftime("%Y-%m-%d %H:%M:%S"),
                week_end.strftime("%Y-%m-%d %H:%M:%S"),
                json.dumps(payload, ensure_ascii=False),
            ),
        )

    try:
        await app.bot.send_message(
            chat_id=s.supervisor_chat_id, text=text, parse_mode="Markdown"
        )
        # Attach JSON for archival
        buf = io.BytesIO(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
        buf.name = f"report_{week_start.date()}_{week_end.date()}.json"
        await app.bot.send_document(chat_id=s.supervisor_chat_id, document=buf)
    except Exception:
        log.exception("Failed to send weekly report to supervisor")
