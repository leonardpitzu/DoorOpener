"""When a PIN works: a guest's dates and uses, and weekly schedules.

A user's rules, as stored and as the admin API sends them::

    {
      "valid_from": "2026-11-10T08:00",   # null = straight away
      "valid_until": "2026-11-12T23:59",  # makes the user a guest, switched off once it passes
      "uses": 3,                          # null = as many as needed
      "schedule": [                       # null = any time; otherwise any one row
        {"days": [0, 1, 2, 3, 4], "hours": ["07:00", "20:00"]},
        {"days": [5, 6], "hours": null}
      ]
    }

In a schedule row, ``days`` runs Monday = 0 ... Sunday = 6 (null: every day) and
``hours`` null means all day; "22:00"-"06:00" runs overnight, and its hours after
midnight belong to the day it opened. No rules (``None``) means always: a
full-time user without a schedule. Only a guest has dates or uses.

One use is one visit. The first opening starts a visit of ``VISIT``, and the
openings within it count once: barrier in, door, barrier out. A visit also
outlasts the end of a schedule window or of a guest's dates, so someone who came
through the barrier at 13:29 can still open the door at 13:31.

Times are the door's local time: a guest's window does not move when the admin
travels.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

# Why a matching PIN was refused, as the audit log records it.
NOT_YET_VALID = "NOT_YET_VALID"
EXPIRED = "EXPIRED"
USED_UP = "USED_UP"
OUTSIDE_SCHEDULE = "OUTSIDE_SCHEDULE"

VISIT = timedelta(minutes=15)
MAX_USES = 1000
MAX_SCHEDULE_ROWS = 20

_KEYS = {"valid_from", "valid_until", "uses", "schedule"}
_ROW_KEYS = {"days", "hours"}
# Before 3.0 a PIN had one row of days and hours, and could be one-time.
_OLD_KEYS = {"days", "hours", "one_time"}


@dataclass(frozen=True)
class Usage:
    """How much of its uses a PIN has had: visits so far, and when the latest began."""

    visits: int = 0
    visit_start: datetime | None = None  # aware


NO_USAGE = Usage()


def normalize(raw: Any) -> dict | None:
    """Validated, canonical rules, or ``None`` for none. Raises ValueError with a readable message."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or not set(raw) <= _KEYS | _OLD_KEYS:
        raise ValueError("Access rules must be an object with " + ", ".join(sorted(_KEYS)))
    raw = _from_old_keys(raw)
    rules = {
        "valid_from": _moment(raw.get("valid_from"), "valid_from"),
        "valid_until": _moment(raw.get("valid_until"), "valid_until"),
        "uses": _uses(raw.get("uses")),
        "schedule": _schedule(raw.get("schedule")),
    }
    if rules["valid_from"] and rules["valid_until"] and rules["valid_from"] >= rules["valid_until"]:
        raise ValueError("valid_until must be after valid_from")
    if (rules["valid_from"] or rules["uses"]) and not rules["valid_until"]:
        raise ValueError("A guest needs an end date (valid_until)")
    return rules if any(rules.values()) else None


def upgrade(stored: Any) -> dict | None:
    """Rules as a store from before 3.0 kept them, in today's shape. Nothing is refused:
    they were valid when written (a one-time PIN, say, needed no end date then)."""
    if not isinstance(stored, dict) or not set(stored) & _OLD_KEYS:
        return stored
    rules = _from_old_keys(stored)
    upgraded = {key: rules.get(key) for key in ("valid_from", "valid_until", "uses", "schedule")}
    return upgraded if any(upgraded.values()) else None


def _from_old_keys(raw: dict) -> dict:
    rules = {key: value for key, value in raw.items() if key in _KEYS}
    if raw.get("days") is not None or raw.get("hours") is not None:
        rules.setdefault("schedule", [{"days": raw.get("days"), "hours": raw.get("hours")}])
    if raw.get("one_time"):
        rules.setdefault("uses", 1)
    return rules


def is_guest(rules: dict | None) -> bool:
    return bool(rules and rules.get("valid_until"))


def in_visit(usage: Usage, now: datetime) -> bool:
    start = usage.visit_start
    if start is None:
        return False
    if now.tzinfo is None:  # a naive time is the door's local time, as everywhere here
        now = now.replace(tzinfo=start.tzinfo)
    return timedelta(0) <= now - start < VISIT


def denial(rules: dict | None, now: datetime, usage: Usage = NO_USAGE) -> str | None:
    """Why the rules refuse a PIN at *now* (the door's local time, aware), or ``None`` if they allow it."""
    if not rules or in_visit(usage, now):
        return None
    local = _local(now)
    if rules.get("valid_from") and local < datetime.fromisoformat(rules["valid_from"]):
        return NOT_YET_VALID
    finished_reason = _finished(rules, usage, local)
    if finished_reason:
        return finished_reason
    schedule = rules.get("schedule")
    if schedule and not any(_row_allows(row, local) for row in schedule):
        return OUTSIDE_SCHEDULE
    return None


def finished(rules: dict | None, usage: Usage, now: datetime) -> str | None:
    """EXPIRED or USED_UP once a guest is done for good (never during a visit), else ``None``."""
    if not rules or in_visit(usage, now):
        return None
    return _finished(rules, usage, _local(now))


def _finished(rules: dict, usage: Usage, local: datetime) -> str | None:
    if rules.get("valid_until") and local >= datetime.fromisoformat(rules["valid_until"]):
        return EXPIRED
    if rules.get("uses") and usage.visits >= rules["uses"]:
        return USED_UP
    return None


def next_window(rules: dict | None, now: datetime) -> datetime | None:
    """When the schedule next lets the PIN in after *now*, within a week (naive local time).

    ``None`` when it never does again: no schedule, or the guest's end date comes first.
    """
    schedule = (rules or {}).get("schedule")
    if not schedule:
        return None
    local = _local(now).replace(second=0, microsecond=0)
    for offset in range(8):
        day = local.date() + timedelta(days=offset)
        starts = [
            datetime.combine(day, time.fromisoformat(row["hours"][0]) if row.get("hours") else time(0, 0))
            for row in schedule
            if row.get("days") is None or day.weekday() in row["days"]
        ]
        later = [start for start in starts if start > local]
        if later:
            soonest = min(later)
            until = rules.get("valid_until")
            return None if until and soonest >= datetime.fromisoformat(until) else soonest
    return None


def _local(now: datetime) -> datetime:
    return now.replace(tzinfo=None)


def _row_allows(row: dict, local: datetime) -> bool:
    day = local.weekday()
    if row.get("hours"):
        start, end = (time.fromisoformat(h) for h in row["hours"])
        moment = local.time()
        if start < end:
            if not start <= moment < end:
                return False
        else:  # overnight: the hours after midnight belong to the day the window opened
            if not (moment >= start or moment < end):
                return False
            if moment < end:
                day = (day - 1) % 7
    return row.get("days") is None or day in row["days"]


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


def _uses(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= MAX_USES:
        raise ValueError(f"uses must be a whole number from 1 to {MAX_USES}, or empty for no limit")
    return value


def _schedule(value: Any) -> list[dict] | None:
    if value in (None, []):
        return None
    if not isinstance(value, list) or len(value) > MAX_SCHEDULE_ROWS:
        raise ValueError(f"schedule must be a list of up to {MAX_SCHEDULE_ROWS} rows of days and hours")
    rows = []
    for row in value:
        if not isinstance(row, dict) or not set(row) <= _ROW_KEYS:
            raise ValueError('Each schedule row must look like {"days": [0, 1], "hours": ["08:00", "18:00"]}')
        rows.append({"days": _days(row.get("days")), "hours": _hours(row.get("hours"))})
    if any(row["days"] is None and row["hours"] is None for row in rows):
        return None  # a row that allows any time makes the whole schedule moot
    return rows


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
