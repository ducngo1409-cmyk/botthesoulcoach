"""Timezone helpers."""

from __future__ import annotations

from datetime import datetime, timezone

import pytz


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_user_tz(dt: datetime, tz_name: str) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(pytz.timezone(tz_name))


def fmt_local(dt: datetime, tz_name: str) -> str:
    return to_user_tz(dt, tz_name).strftime("%Y-%m-%d %H:%M %Z")
