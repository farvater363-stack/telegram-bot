"""
Application configuration helpers.

Controls bot token, admin list, timezone, and scheduled announcement data.
Environment variables override the defaults below.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from typing import List
from zoneinfo import ZoneInfo


def _parse_id_list(value: str | None) -> List[int]:
    if not value:
        return []
    ids: List[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            ids.append(int(item))
        except ValueError:
            continue
    return ids


def _parse_admin_ids(value: str | None) -> List[int]:
    return _parse_id_list(value)


def _parse_announcement_days(raw: str | None) -> List[int]:
    """
    Accepts comma separated weekday names (Mon, Monday, 0, etc.) and returns 0-6 ints.
    """
    if not raw:
        raw = "Monday,Thursday"
    mapping = {
        "0": 0,
        "1": 1,
        "2": 2,
        "3": 3,
        "4": 4,
        "5": 5,
        "6": 6,
        "MON": 0,
        "MONDAY": 0,
        "TUE": 1,
        "TUESDAY": 1,
        "WED": 2,
        "WEDNESDAY": 2,
        "THU": 3,
        "THURSDAY": 3,
        "FRI": 4,
        "FRIDAY": 4,
        "SAT": 5,
        "SATURDAY": 5,
        "SUN": 6,
        "SUNDAY": 6,
    }
    days: List[int] = []
    for part in raw.split(","):
        key = part.strip().upper()
        if not key:
            continue
        if key not in mapping:
            continue
        days.append(mapping[key])
    return days or []


def _parse_timezone(value: str | None) -> ZoneInfo:
    tz_name = value or "UTC"
    try:
        return ZoneInfo(tz_name)
    except Exception:  # pragma: no cover - direct user input
        return ZoneInfo("UTC")

def _default_webapp_host() -> str:
    """Prefer explicit WEBAPP_HOST, fallback to generic HOST (Railway) or localhost."""
    return os.getenv("WEBAPP_HOST") or os.getenv("HOST") or "0.0.0.0"

def _default_webapp_port() -> int:
    """Prefer WEBAPP_PORT, fallback to provider PORT (Railway/Render/etc.)."""
    raw = os.getenv("WEBAPP_PORT") or os.getenv("PORT") or "8080"
    try:
        return int(raw)
    except ValueError:
        return 8080



@dataclass(slots=True)
class Settings:
    bot_token: str = field(
        default_factory=lambda: os.getenv("BOT_TOKEN") or ""
    )
    admin_ids: List[int] = field(
        default_factory=lambda: _parse_admin_ids(os.getenv("ADMIN_IDS", "140802473"))
    )
    timezone: ZoneInfo = field(
        default_factory=lambda: _parse_timezone(os.getenv("TIMEZONE", "America/New_York"))
    )
    database_path: str = field(default_factory=lambda: os.getenv("DATABASE_PATH", "bot.db"))
    announcement_days: List[int] = field(
        default_factory=lambda: _parse_announcement_days(os.getenv("ANNOUNCEMENT_DAYS"))
    )
    announcement_time: str = field(default_factory=lambda: os.getenv("ANNOUNCEMENT_TIME", "10:00"))
    broadcast_retry_count: int = field(
        default_factory=lambda: int(os.getenv("BROADCAST_RETRY_COUNT", "3"))
    )
    broadcast_retry_delay: float = field(
        default_factory=lambda: float(os.getenv("BROADCAST_RETRY_DELAY", "2"))
    )
    approved_chat_ids: List[int] = field(
        default_factory=lambda: _parse_id_list(os.getenv("APPROVED_CHAT_IDS", "-5035929357"))
    )
    referrals_per_cpm_step: int = field(
        default_factory=lambda: int(os.getenv("REFERRALS_PER_CPM_STEP", "2"))
    )
    cpm_step_value: int = field(default_factory=lambda: int(os.getenv("CPM_STEP_VALUE", "2")))
    referral_cash_bonus: int = field(
        default_factory=lambda: int(os.getenv("REFERRAL_CASH_BONUS", "500"))
    )
    webapp_host: str = field(default_factory=_default_webapp_host)
    webapp_port: int = field(default_factory=_default_webapp_port)
    webapp_url: str = field(default_factory=lambda: os.getenv("WEBAPP_URL", "http://127.0.0.1:8080/"))
    webapp_debug_secret: str = field(default_factory=lambda: os.getenv("WEBAPP_DEBUG_SECRET", ""))

    @property
    def announcement_time_of_day(self) -> time:
        hours, minutes = self.announcement_time.split(":")
        return time(hour=int(hours), minute=int(minutes))

    @property
    def primary_admin_id(self) -> int | None:
        return self.admin_ids[0] if self.admin_ids else None


settings = Settings()


def is_admin(user_id: int | None) -> bool:
    """
    Fast helper for checking administrator permissions.
    """
    return bool(user_id) and user_id in settings.admin_ids


def is_primary_admin(user_id: int | None) -> bool:
    return bool(user_id) and settings.primary_admin_id == user_id


