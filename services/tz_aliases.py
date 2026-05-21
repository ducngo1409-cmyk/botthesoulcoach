"""Resolve user-friendly timezone input to a valid IANA name.

Users rarely type 'Asia/Ho_Chi_Minh' verbatim. Accept country/city aliases,
case-insensitive spelling variants (with or without diacritics), and UTC
offsets like '+7', 'UTC+7', 'GMT-5'.

Returns the canonical IANA name, or None if we can't resolve it.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

import pytz

# Map normalized input (lowercase, ASCII, single-spaced) → IANA name.
_ALIASES: dict[str, str] = {
    # Vietnam
    "vn": "Asia/Ho_Chi_Minh",
    "viet nam": "Asia/Ho_Chi_Minh",
    "vietnam": "Asia/Ho_Chi_Minh",
    "ha noi": "Asia/Ho_Chi_Minh",
    "hanoi": "Asia/Ho_Chi_Minh",
    "hn": "Asia/Ho_Chi_Minh",
    "ho chi minh": "Asia/Ho_Chi_Minh",
    "hochiminh": "Asia/Ho_Chi_Minh",
    "hcm": "Asia/Ho_Chi_Minh",
    "saigon": "Asia/Ho_Chi_Minh",
    "sai gon": "Asia/Ho_Chi_Minh",
    "tphcm": "Asia/Ho_Chi_Minh",
    "da nang": "Asia/Ho_Chi_Minh",
    "danang": "Asia/Ho_Chi_Minh",
    # Asia
    "singapore": "Asia/Singapore",
    "sg": "Asia/Singapore",
    "bangkok": "Asia/Bangkok",
    "thailand": "Asia/Bangkok",
    "kuala lumpur": "Asia/Kuala_Lumpur",
    "malaysia": "Asia/Kuala_Lumpur",
    "jakarta": "Asia/Jakarta",
    "indonesia": "Asia/Jakarta",
    "manila": "Asia/Manila",
    "philippines": "Asia/Manila",
    "tokyo": "Asia/Tokyo",
    "japan": "Asia/Tokyo",
    "jp": "Asia/Tokyo",
    "seoul": "Asia/Seoul",
    "korea": "Asia/Seoul",
    "kr": "Asia/Seoul",
    "beijing": "Asia/Shanghai",
    "shanghai": "Asia/Shanghai",
    "china": "Asia/Shanghai",
    "cn": "Asia/Shanghai",
    "hong kong": "Asia/Hong_Kong",
    "hongkong": "Asia/Hong_Kong",
    "hk": "Asia/Hong_Kong",
    "taipei": "Asia/Taipei",
    "taiwan": "Asia/Taipei",
    "india": "Asia/Kolkata",
    "delhi": "Asia/Kolkata",
    "mumbai": "Asia/Kolkata",
    # Europe
    "london": "Europe/London",
    "uk": "Europe/London",
    "england": "Europe/London",
    "paris": "Europe/Paris",
    "france": "Europe/Paris",
    "berlin": "Europe/Berlin",
    "germany": "Europe/Berlin",
    "amsterdam": "Europe/Amsterdam",
    "netherlands": "Europe/Amsterdam",
    "madrid": "Europe/Madrid",
    "spain": "Europe/Madrid",
    "rome": "Europe/Rome",
    "italy": "Europe/Rome",
    "moscow": "Europe/Moscow",
    "russia": "Europe/Moscow",
    # Americas
    "new york": "America/New_York",
    "newyork": "America/New_York",
    "ny": "America/New_York",
    "nyc": "America/New_York",
    "los angeles": "America/Los_Angeles",
    "la": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles",
    "sf": "America/Los_Angeles",
    "chicago": "America/Chicago",
    "toronto": "America/Toronto",
    "canada": "America/Toronto",
    "mexico": "America/Mexico_City",
    # Oceania
    "sydney": "Australia/Sydney",
    "australia": "Australia/Sydney",
    "melbourne": "Australia/Melbourne",
    "auckland": "Pacific/Auckland",
    "new zealand": "Pacific/Auckland",
    "nz": "Pacific/Auckland",
    # UTC
    "utc": "UTC",
    "gmt": "UTC",
}


def _strip_diacritics(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def _normalize(text: str) -> str:
    text = _strip_diacritics(text.lower())
    text = text.replace("_", " ").replace("-", " ").replace(",", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


_OFFSET_RE = re.compile(r"^(?:utc|gmt)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?$")


def _parse_offset(text: str) -> Optional[str]:
    """Accept '+7', 'UTC+7', 'GMT-5', '+07:00' → Etc/GMT-N (sign is POSIX-flipped)."""
    m = _OFFSET_RE.match(text.lower().strip())
    if not m:
        return None
    sign, hours, mins = m.groups()
    h = int(hours)
    if mins and int(mins) != 0:
        return None  # Etc/GMT-X only supports whole-hour offsets
    if h == 0:
        return "UTC"
    # POSIX flips the sign: UTC+7 → Etc/GMT-7
    flipped = "-" if sign == "+" else "+"
    name = f"Etc/GMT{flipped}{h}"
    try:
        pytz.timezone(name)
        return name
    except pytz.UnknownTimeZoneError:
        return None


def resolve_tz(text: str) -> Optional[str]:
    """Best-effort resolve user input to an IANA timezone name.

    Tries in order: exact IANA, alias dict (normalized), UTC offset.
    Returns None if nothing matches.
    """
    if not text:
        return None
    raw = text.strip()

    # 1. Try exact IANA name (handles 'Asia/Ho_Chi_Minh' and similar).
    try:
        pytz.timezone(raw)
        return raw
    except pytz.UnknownTimeZoneError:
        pass

    # 2. Try normalized alias lookup.
    norm = _normalize(raw)
    if norm in _ALIASES:
        return _ALIASES[norm]

    # 3. Try UTC offset.
    return _parse_offset(raw)
