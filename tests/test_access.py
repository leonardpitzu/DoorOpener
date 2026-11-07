"""Access rules: guests' dates and uses, schedules, and visits."""
from datetime import datetime, timedelta, timezone

import pytest

from access import (
    EXPIRED, NOT_YET_VALID, OUTSIDE_SCHEDULE, USED_UP, VISIT, Usage, denial, finished, in_visit, is_guest,
    next_window, normalize, upgrade,
)

WED_NOON = datetime(2026, 11, 11, 12, 0)  # a Wednesday
UNTIL_FRIDAY = "2026-11-13T23:59"


def at(day: int, hhmm: str) -> datetime:
    """Monday 9 Nov 2026 plus *day* days, at *hhmm*."""
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 11, 9 + day, hour, minute)


def row(days=None, hours=None):
    return {"days": days, "hours": hours}


def test_no_rules_always_allow():
    assert normalize(None) is None
    assert normalize({}) is None
    assert normalize({"schedule": [row([0, 1, 2, 3, 4, 5, 6])]}) is None  # every day, all day is no limit
    assert normalize({"schedule": [row([0], ["08:00", "12:00"]), row()]}) is None  # one row allows any time
    assert normalize({"schedule": []}) is None
    assert denial(None, WED_NOON) is None


def test_rules_are_canonical():
    rules = normalize({"valid_from": "2026-11-10T08:00:30", "valid_until": UNTIL_FRIDAY, "uses": 3,
                       "schedule": [row([4, 0, 4], ["08:00:00", "18:00"])]})
    assert rules == {"valid_from": "2026-11-10T08:00", "valid_until": UNTIL_FRIDAY, "uses": 3,
                     "schedule": [{"days": [0, 4], "hours": ["08:00", "18:00"]}]}


@pytest.mark.parametrize("raw, message", [
    ("always", "object"),
    ({"colour": "blue"}, "object"),
    ({"valid_from": "tomorrow", "valid_until": UNTIL_FRIDAY}, "valid_from"),
    ({"valid_until": "2026-11-12T18:00+02:00"}, "valid_until"),  # local time only
    ({"valid_from": "2026-11-12T18:00", "valid_until": "2026-11-12T08:00"}, "after"),
    ({"uses": 1}, "end date"),  # a courier without an end date would never be switched off
    ({"valid_from": "2026-11-12T08:00"}, "end date"),
    ({"uses": 0, "valid_until": UNTIL_FRIDAY}, "uses"),
    ({"uses": "3", "valid_until": UNTIL_FRIDAY}, "uses"),
    ({"uses": True, "valid_until": UNTIL_FRIDAY}, "uses"),
    ({"schedule": "weekdays"}, "schedule"),
    ({"schedule": [{"days": [0], "colour": "blue"}]}, "row"),
    ({"schedule": [row([])]}, "days"),
    ({"schedule": [row([7])]}, "days"),
    ({"schedule": [row([True])]}, "days"),
    ({"schedule": [row(hours=["08:00"])]}, "hours"),
    ({"schedule": [row(hours=["8am", "6pm"])]}, "hours"),
    ({"schedule": [row(hours=["08:00", "08:00"])]}, "different"),
    ({"schedule": [row([0], ["08:00", "09:00"])] * 21}, "up to 20"),
])
def test_bad_rules_are_refused_with_a_reason(raw, message):
    with pytest.raises(ValueError, match=message):
        normalize(raw)


def test_a_guest_is_someone_with_an_end_date():
    assert is_guest(normalize({"valid_until": UNTIL_FRIDAY}))
    assert not is_guest(normalize({"schedule": [row([0, 1, 2, 3, 4], ["07:00", "20:00"])]}))
    assert not is_guest(None)


def test_validity_dates():
    rules = normalize({"valid_from": "2026-11-10T08:00", "valid_until": "2026-11-12T18:00"})
    assert denial(rules, datetime(2026, 11, 10, 7, 59)) == NOT_YET_VALID
    assert denial(rules, datetime(2026, 11, 10, 8, 0)) is None
    assert denial(rules, datetime(2026, 11, 12, 17, 59)) is None
    assert denial(rules, datetime(2026, 11, 12, 18, 0)) == EXPIRED


def test_weekdays_and_hours():
    cleaner = normalize({"schedule": [row([0, 1, 2, 3, 4], ["08:00", "18:00"])]})
    assert denial(cleaner, at(2, "12:00")) is None              # Wednesday noon
    assert denial(cleaner, at(2, "07:59")) == OUTSIDE_SCHEDULE
    assert denial(cleaner, at(2, "18:00")) == OUTSIDE_SCHEDULE   # the end is exclusive
    assert denial(cleaner, at(5, "12:00")) == OUTSIDE_SCHEDULE   # Saturday


def test_any_row_of_a_schedule_lets_the_pin_in():
    kids = normalize({"schedule": [row([0, 1, 2, 3, 4], ["07:00", "08:30"]), row([0, 1, 2, 3, 4], ["13:00", "20:00"]),
                                   row([5, 6], ["09:00", "21:00"])]})
    assert denial(kids, at(2, "07:30")) is None
    assert denial(kids, at(2, "10:00")) == OUTSIDE_SCHEDULE      # at school
    assert denial(kids, at(2, "19:59")) is None
    assert denial(kids, at(5, "07:30")) == OUTSIDE_SCHEDULE      # Saturday morning, too early
    assert denial(kids, at(5, "20:30")) is None


def test_overnight_hours_belong_to_the_evening_they_start():
    friday_nights = normalize({"schedule": [row([4], ["22:00", "06:00"])]})
    assert denial(friday_nights, at(4, "23:00")) is None    # Friday 23:00
    assert denial(friday_nights, at(5, "05:59")) is None    # still Friday night, on Saturday
    assert denial(friday_nights, at(5, "06:00")) == OUTSIDE_SCHEDULE
    assert denial(friday_nights, at(4, "05:00")) == OUTSIDE_SCHEDULE  # Thursday night
    assert denial(friday_nights, at(4, "12:00")) == OUTSIDE_SCHEDULE


def test_the_door_clock_is_what_counts():
    # An aware time is read as the door's local time, whatever its zone says.
    from zoneinfo import ZoneInfo
    rules = normalize({"schedule": [row(hours=["08:00", "18:00"])]})
    assert denial(rules, datetime(2026, 11, 11, 12, 0, tzinfo=ZoneInfo("Europe/Bucharest"))) is None


# ---------------------------------------------------------------------------
# Uses and visits
# ---------------------------------------------------------------------------
UTC = timezone.utc
MONDAY_10 = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
COURIER = {"valid_until": "2026-11-09T23:59", "uses": 1, "schedule": [row([0], ["10:00", "13:30"])]}


def test_one_use_is_one_visit():
    courier = normalize(COURIER)
    assert denial(courier, MONDAY_10, Usage()) is None
    visiting = Usage(visits=1, visit_start=MONDAY_10)
    assert in_visit(visiting, MONDAY_10 + timedelta(minutes=14))
    assert denial(courier, MONDAY_10 + timedelta(minutes=14), visiting) is None  # the door after the barrier
    assert denial(courier, MONDAY_10 + VISIT, visiting) == USED_UP               # a second visit
    assert finished(courier, visiting, MONDAY_10 + timedelta(minutes=5)) is None  # never mid-visit
    assert finished(courier, visiting, MONDAY_10 + VISIT) == USED_UP


def test_a_visit_outlasts_the_window_it_began_in():
    courier = normalize(COURIER)
    late = datetime(2026, 11, 9, 13, 29, tzinfo=UTC)
    visiting = Usage(visits=1, visit_start=late)
    assert denial(courier, late + timedelta(minutes=2), visiting) is None
    assert denial(courier, late + timedelta(minutes=2), Usage()) == OUTSIDE_SCHEDULE


def test_an_end_date_finishes_a_guest():
    friend = normalize({"valid_until": "2026-11-15T23:59"})
    assert finished(friend, Usage(visits=40), datetime(2026, 11, 15, 23, 58, tzinfo=UTC)) is None
    assert finished(friend, Usage(), datetime(2026, 11, 15, 23, 59, tzinfo=UTC)) == EXPIRED
    assert finished(None, Usage(), MONDAY_10) is None  # full-time users never finish


def test_a_visit_from_a_clock_set_back_does_not_count():
    assert not in_visit(Usage(visits=1, visit_start=MONDAY_10 + timedelta(minutes=5)), MONDAY_10)


# ---------------------------------------------------------------------------
# The next time a schedule lets a PIN in (what its owner is told)
# ---------------------------------------------------------------------------
def test_next_window():
    kids = normalize({"schedule": [row([0, 1, 2, 3, 4], ["07:00", "20:00"]), row([5, 6], ["09:00", "21:00"])]})
    assert next_window(kids, at(2, "22:00")) == at(3, "07:00")   # Wednesday night: Thursday morning
    assert next_window(kids, at(2, "06:00")) == at(2, "07:00")   # later the same day
    assert next_window(kids, at(4, "21:00")) == at(5, "09:00")   # Friday night: Saturday at 9
    weekends = normalize({"schedule": [row([5, 6])]})
    assert next_window(weekends, at(2, "12:00")) == at(5, "00:00")
    assert next_window(None, WED_NOON) is None


def test_no_next_window_after_the_end_date():
    guest = normalize({"valid_until": "2026-11-11T23:59", "schedule": [row([0, 1, 2, 3, 4], ["08:00", "18:00"])]})
    assert next_window(guest, at(2, "19:00")) is None


# ---------------------------------------------------------------------------
# Rules from before 3.0
# ---------------------------------------------------------------------------
def test_rules_from_before_3_0_are_upgraded():
    old = {"valid_from": None, "valid_until": "2026-11-12T18:00", "days": [0, 4], "hours": ["08:00", "18:00"],
           "one_time": True}
    assert upgrade(old) == {"valid_from": None, "valid_until": "2026-11-12T18:00", "uses": 1,
                            "schedule": [{"days": [0, 4], "hours": ["08:00", "18:00"]}]}
    # A one-time PIN needed no end date then; it keeps working as it did.
    assert upgrade({"valid_from": None, "valid_until": None, "days": None, "hours": None, "one_time": True}) == {
        "valid_from": None, "valid_until": None, "uses": 1, "schedule": None}
    assert upgrade(None) is None
    current = normalize({"valid_until": UNTIL_FRIDAY})
    assert upgrade(current) is current


def test_the_old_keys_still_work_through_the_api():
    assert normalize({"days": [0], "hours": ["08:00", "09:00"]}) == {
        "valid_from": None, "valid_until": None, "uses": None,
        "schedule": [{"days": [0], "hours": ["08:00", "09:00"]}]}
