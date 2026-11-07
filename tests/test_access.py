"""Access rules: when a PIN works."""
from datetime import datetime

import pytest

from access import EXPIRED, NOT_YET_VALID, OUTSIDE_SCHEDULE, denial, is_one_time, normalize

WED_NOON = datetime(2026, 11, 11, 12, 0)  # a Wednesday


def at(day: int, hhmm: str) -> datetime:
    """Monday 9 Nov 2026 plus *day* days, at *hhmm*."""
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 11, 9 + day, hour, minute)


def test_no_rules_always_allow():
    assert normalize(None) is None
    assert normalize({}) is None
    assert normalize({"days": [0, 1, 2, 3, 4, 5, 6], "one_time": False}) is None  # every day is no limit
    assert denial(None, WED_NOON) is None


def test_rules_are_canonical():
    rules = normalize({"valid_from": "2026-11-10T08:00:30", "days": [4, 0, 4], "hours": ["08:00:00", "18:00"]})
    assert rules == {"valid_from": "2026-11-10T08:00", "valid_until": None, "days": [0, 4],
                     "hours": ["08:00", "18:00"], "one_time": False}


@pytest.mark.parametrize("raw, message", [
    ("always", "object"),
    ({"colour": "blue"}, "object"),
    ({"valid_from": "tomorrow"}, "valid_from"),
    ({"valid_until": "2026-11-12T18:00+02:00"}, "valid_until"),  # local time only
    ({"valid_from": "2026-11-12T18:00", "valid_until": "2026-11-12T08:00"}, "after"),
    ({"days": []}, "days"),
    ({"days": [7]}, "days"),
    ({"days": [True]}, "days"),
    ({"hours": ["08:00"]}, "hours"),
    ({"hours": ["8am", "6pm"]}, "hours"),
    ({"hours": ["08:00", "08:00"]}, "different"),
    ({"one_time": "yes"}, "one_time"),
])
def test_bad_rules_are_refused_with_a_reason(raw, message):
    with pytest.raises(ValueError, match=message):
        normalize(raw)


def test_validity_dates():
    rules = normalize({"valid_from": "2026-11-10T08:00", "valid_until": "2026-11-12T18:00"})
    assert denial(rules, datetime(2026, 11, 10, 7, 59)) == NOT_YET_VALID
    assert denial(rules, datetime(2026, 11, 10, 8, 0)) is None
    assert denial(rules, datetime(2026, 11, 12, 17, 59)) is None
    assert denial(rules, datetime(2026, 11, 12, 18, 0)) == EXPIRED


def test_weekdays_and_hours():
    cleaner = normalize({"days": [0, 1, 2, 3, 4], "hours": ["08:00", "18:00"]})
    assert denial(cleaner, at(2, "12:00")) is None              # Wednesday noon
    assert denial(cleaner, at(2, "07:59")) == OUTSIDE_SCHEDULE
    assert denial(cleaner, at(2, "18:00")) == OUTSIDE_SCHEDULE   # the end is exclusive
    assert denial(cleaner, at(5, "12:00")) == OUTSIDE_SCHEDULE   # Saturday


def test_overnight_hours_belong_to_the_evening_they_start():
    friday_nights = normalize({"days": [4], "hours": ["22:00", "06:00"]})
    assert denial(friday_nights, at(4, "23:00")) is None    # Friday 23:00
    assert denial(friday_nights, at(5, "05:59")) is None    # still Friday night, on Saturday
    assert denial(friday_nights, at(5, "06:00")) == OUTSIDE_SCHEDULE
    assert denial(friday_nights, at(4, "05:00")) == OUTSIDE_SCHEDULE  # Thursday night
    assert denial(friday_nights, at(4, "12:00")) == OUTSIDE_SCHEDULE


def test_the_door_clock_is_what_counts():
    # An aware time is read as the door's local time, whatever its zone says.
    from zoneinfo import ZoneInfo
    rules = normalize({"hours": ["08:00", "18:00"]})
    assert denial(rules, datetime(2026, 11, 11, 12, 0, tzinfo=ZoneInfo("Europe/Bucharest"))) is None


def test_one_time():
    assert is_one_time(normalize({"one_time": True}))
    assert not is_one_time(normalize({"valid_until": "2026-11-12T18:00"}))
    assert not is_one_time(None)
