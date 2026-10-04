"""Time helpers. All storage is UTC; market rules use America/New_York."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_et(moment: datetime) -> datetime:
    return moment.astimezone(ET)


def et_date(moment: datetime) -> date:
    return to_et(moment).date()


def is_weekday(moment: datetime) -> bool:
    return to_et(moment).weekday() < 5


def within_session(moment: datetime, open_et: time, close_et: time) -> bool:
    """Regular session check (weekdays only; exchange holidays not modelled)."""
    local = to_et(moment)
    if local.weekday() >= 5:
        return False
    return open_et <= local.time() < close_et


def et_datetime(day: date, at: time) -> datetime:
    return datetime.combine(day, at, tzinfo=ET)


def display_time(moment: datetime, tz_name: str) -> str:
    """e.g. '8:14 PM' in the display timezone."""
    local = moment.astimezone(ZoneInfo(tz_name))
    return local.strftime("%I:%M %p").lstrip("0")
