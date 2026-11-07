"""When a PIN works: validity dates, weekdays and hours, and one-time PINs.

A user's rules, as stored and as the admin API sends them::

    {
      "valid_from": "2026-11-10T08:00",   # local time at the door; null = no start
      "valid_until": "2026-11-12T18:00",  # null = no end
      "days": [0, 1, 2, 3, 4],            # Monday = 0 ... Sunday = 6; null = every day
      "hours": ["08:00", "18:00"],        # null = all day; "22:00"-"06:00" runs overnight
      "one_time": false                   # deactivated after the first opening
    }

No rules (``None``) means the PIN always works. Times are the door's local time:
a guest's window does not move when the admin is travelling.
"""

from datetime import datetime, time
from typing import Any

# Why a matching PIN was refused, as the audit log records it.
NOT_YET_VALID = "NOT_YET_VALID"
EXPIRED = "EXPIRED"
OUTSIDE_SCHEDULE = "OUTSIDE_SCHEDULE"

_KEYS = {"valid_from", "valid_until", "days", "hours", "one_time"}


def normalize(raw: Any) -> dict | None:
    """Validated, canonical rules, or ``None`` for none. Raises ValueError with a readable message."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or not set(raw) <= _KEYS:
        raise ValueError("Access rules must be an object with " + ", ".join(sorted(_KEYS)))
    rules = {
        "valid_from": _moment(raw.get("valid_from"), "valid_from"),
        "valid_until": _moment(raw.get("valid_until"), "valid_until"),
        "days": _days(raw.get("days")),
        "hours": _hours(raw.get("hours")),
        "one_time": _flag(raw.get("one_time")),
    }
    if rules["valid_from"] and rules["valid_until"] and rules["valid_from"] >= rules["valid_until"]:
        raise ValueError("valid_until must be after valid_from")
    if not any(rules.values()):
        return None
    return rules


def denial(rules: dict | None, now: datetime) -> str | None:
    """Why the rules refuse a PIN at *now* (the door's local time), or ``None`` if they allow it."""
    if not rules:
        return None
    local = now.replace(tzinfo=None)
    if rules.get("valid_from") and local < datetime.fromisoformat(rules["valid_from"]):
        return NOT_YET_VALID
    if rules.get("valid_until") and local >= datetime.fromisoformat(rules["valid_until"]):
        return EXPIRED
    day = local.weekday()
    if rules.get("hours"):
        start, end = (time.fromisoformat(h) for h in rules["hours"])
        moment = local.time()
        if start < end:
            inside = start <= moment < end
        else:  # overnight: the hours after midnight belong to the day the window opened
            inside = moment >= start or moment < end
            if moment < end:
                day = (day - 1) % 7
        if not inside:
            return OUTSIDE_SCHEDULE
    if rules.get("days") is not None and day not in rules["days"]:
        return OUTSIDE_SCHEDULE
    return None


def is_one_time(rules: dict | None) -> bool:
    return bool(rules and rules.get("one_time"))


def _moment(value: Any, name: str) -> str | None:
    if value in (None, ""):
        return None
    try:
        moment = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        moment = None
    if moment is None or moment.tzinfo is not None:
        raise ValueError(f"{name} must be a local date and time like 2026-11-10T08:00")
    return moment.isoformat(timespec="minutes")


def _days(value: Any) -> list[int] | None:
    if value is None:
        return None
    if (not isinstance(value, list) or not value
            or not all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in value)):
        raise ValueError("days must be a non-empty list of weekdays, Monday = 0 to Sunday = 6")
    days = sorted(set(value))
    return None if len(days) == 7 else days


def _hours(value: Any) -> list[str] | None:
    if value is None:
        return None
    message = 'hours must be two local times like ["08:00", "18:00"]'
    if not (isinstance(value, list) and len(value) == 2 and all(isinstance(h, str) for h in value)):
        raise ValueError(message)
    try:
        start, end = (time.fromisoformat(h) for h in value)
    except ValueError:
        raise ValueError(message) from None
    if start.tzinfo or end.tzinfo:
        raise ValueError(message)
    if start == end:
        raise ValueError("hours must start and end at different times")
    return [start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")]


def _flag(value: Any) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ValueError("one_time must be true or false")
    return value
