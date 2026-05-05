"""Centralized configuration. Loads .env at import time."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")


def _req(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    supervisor_chat_id: int
    gemini_api_key: str
    gemini_model: str
    db_path: Path
    default_tz: str
    reminder_nudge_hours: int
    reminder_miss_hours: int
    report_cron: str
    fuzzy_threshold: int          # 0..100, rapidfuzz scale
    sat_threshold: int
    log_level: str
    health_port: int
    project_root: Path


def load_settings() -> Settings:
    db_path = Path(os.getenv("DB_PATH", "data/soul_coach.db"))
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

    return Settings(
        telegram_token=_req("TELEGRAM_TOKEN"),
        supervisor_chat_id=int(_req("SUPERVISOR_CHAT_ID")),
        gemini_api_key=_req("GEMINI_API_KEY"),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-1.5-flash"),
        db_path=db_path,
        default_tz=os.getenv("DEFAULT_TZ", "Asia/Ho_Chi_Minh"),
        reminder_nudge_hours=_int("REMINDER_NUDGE_HOURS", 12),
        reminder_miss_hours=_int("REMINDER_MISS_HOURS", 24),
        report_cron=os.getenv("REPORT_CRON", "0 18 * * SUN"),
        fuzzy_threshold=_int("FUZZY_THRESHOLD", 70),
        sat_threshold=_int("SAT_THRESHOLD", 10),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        health_port=_int("HEALTH_PORT", 8080),
        project_root=PROJECT_ROOT,
    )


# Settings is lazily loaded at startup so tests can stub .env first.
_settings: Settings | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings
