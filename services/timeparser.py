"""Translate user-friendly time expressions into 5-field cron expressions.

Most users don't think in cron. Accept inputs like:

    "22:30"                 → "30 22 * * *"   (every day at 22:30)
    "daily 22:30"           → "30 22 * * *"
    "weekdays 9:00"         → "0 9 * * 1-5"
    "weekends 10:00"        → "0 10 * * 0,6"
    "every monday 8:00"     → "0 8 * * 1"
    "every 6 hours"         → "0 */6 * * *"
    "every 30 minutes"      → "*/30 * * * *"
    "0 8 * * *"             → passthrough (already cron)

Returns (cron_expr, human_summary) or (None, error_message).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional, Tuple

from apscheduler.triggers.cron import CronTrigger

_DOW = {
    # English
    "sun": 0, "sunday": 0,
    "mon": 1, "monday": 1,
    "tue": 2, "tues": 2, "tuesday": 2,
    "wed": 3, "wednesday": 3,
    "thu": 4, "thur": 4, "thurs": 4, "thursday": 4,
    "fri": 5, "friday": 5,
    "sat": 6, "saturday": 6,
    # Vietnamese
    "cn": 0, "chu nhat": 0, "chunhat": 0,
    "t2": 1, "thu hai": 1, "thuhai": 1,
    "t3": 2, "thu ba": 2, "thuba": 2,
    "t4": 3, "thu tu": 3, "thutu": 3,
    "t5": 4, "thu nam": 4, "thunam": 4,
    "t6": 5, "thu sau": 5, "thusau": 5,
    "t7": 6, "thu bay": 6, "thubay": 6,
}


def _strip_diacritics(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def _norm(s: str) -> str:
    s = _strip_diacritics(s.lower())
    s = re.sub(r"\s+", " ", s).strip()
    return s


_TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_EVERY_N_HOURS = re.compile(r"\bevery\s+(\d+)\s*(?:h|hours?|gio|tieng)\b")
_EVERY_N_MIN = re.compile(r"\bevery\s+(\d+)\s*(?:m|min|mins|minutes?|phut)\b")


def parse(text: str) -> Tuple[Optional[str], str]:
    """Return (cron_expr, summary). On failure, cron_expr is None and summary is the error."""
    if not text or not text.strip():
        return None, "trống"

    raw = text.strip()

    # 1. Already valid cron? Pass through.
    if _looks_like_cron(raw):
        try:
            CronTrigger.from_crontab(raw)
            return raw, _summarize_cron(raw)
        except Exception:
            pass  # malformed cron — fall through to friendly parsing

    n = _norm(raw)

    # 2. "every N hours" / "every N minutes" / "moi N gio"
    m = _EVERY_N_HOURS.search(n)
    if m:
        h = int(m.group(1))
        if 1 <= h <= 23:
            return f"0 */{h} * * *", f"mỗi {h} giờ"
    m = _EVERY_N_MIN.search(n)
    if m:
        mins = int(m.group(1))
        if 1 <= mins <= 59:
            return f"*/{mins} * * * *", f"mỗi {mins} phút"

    # 3. Look for HH:MM somewhere; determine day mask from context.
    tm = _TIME_RE.search(n)
    if tm:
        hh, mm = int(tm.group(1)), int(tm.group(2))
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return None, "giờ:phút không hợp lệ"
        dow = _detect_dow(n)
        if dow == "*":
            return f"{mm} {hh} * * *", f"mỗi ngày lúc {hh:02d}:{mm:02d}"
        return f"{mm} {hh} * * {dow}", f"{_dow_label(dow)} lúc {hh:02d}:{mm:02d}"

    return None, _help_text()


def _looks_like_cron(s: str) -> bool:
    parts = s.split()
    return len(parts) == 5 and all(re.match(r"^[\d\*/,\-]+$", p) for p in parts)


def _detect_dow(text: str) -> str:
    """Return cron day-of-week field from natural language. '*' = every day."""
    if "weekday" in text or "ngay thuong" in text or "ngay lam" in text:
        return "1-5"
    if "weekend" in text or "cuoi tuan" in text:
        return "0,6"
    if "daily" in text or "every day" in text or "moi ngay" in text or "hang ngay" in text:
        return "*"
    # Single days
    nums = []
    for tok in re.split(r"[\s,]+", text):
        if tok in _DOW:
            nums.append(_DOW[tok])
    if nums:
        nums = sorted(set(nums))
        return ",".join(str(n) for n in nums)
    return "*"


def _dow_label(dow: str) -> str:
    if dow == "*":
        return "mỗi ngày"
    if dow == "1-5":
        return "thứ 2 đến thứ 6"
    if dow == "0,6":
        return "cuối tuần"
    name_map = {
        "0": "chủ nhật", "1": "thứ 2", "2": "thứ 3", "3": "thứ 4",
        "4": "thứ 5", "5": "thứ 6", "6": "thứ 7",
    }
    parts = [name_map.get(d, d) for d in dow.split(",")]
    return ", ".join(parts)


def _summarize_cron(expr: str) -> str:
    parts = expr.split()
    if len(parts) != 5:
        return expr
    mm, hh, dom, mon, dow = parts
    if dom == "*" and mon == "*":
        if mm.isdigit() and hh.isdigit():
            return f"{_dow_label(dow)} lúc {int(hh):02d}:{int(mm):02d}"
    return expr


def _help_text() -> str:
    return (
        "Không hiểu thời gian này. Thử một trong các cách sau:\n\n"
        "🕐 *Cách 1 — đơn giản nhất*\n"
        "`/addtask Thiền | daily 7:00`\n"
        "`/addtask Uống nước | every 3 hours`\n"
        "`/addtask Báo cáo | weekdays 17:30`\n"
        "`/addtask Đi bộ | weekends 6:30`\n"
        "`/addtask Họp | every monday 9:00`\n\n"
        "🕑 *Cách 2 — cron 5 trường*\n"
        "`phút giờ ngày tháng thứ`\n"
        "• `30 22 * * *` — mỗi ngày 22:30\n"
        "• `0 9 * * 1-5` — T2-T6 lúc 9:00\n"
        "• `0 */6 * * *` — mỗi 6 giờ\n"
        "• `15 7 * * 1,3,5` — T2/T4/T6 lúc 7:15"
    )
