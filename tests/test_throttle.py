"""Throttle backoff and the failure alarm, driven by a fake clock."""
from datetime import datetime, timedelta, timezone

import pytest

import security
from security import MAX_BLOCK_TIME, FailureAlarm, Throttle

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
BLOCK = timedelta(minutes=5)


@pytest.fixture
def clock(monkeypatch):
    now = {"t": T0}
    monkeypatch.setattr(security, "get_current_time", lambda: now["t"])

    def advance(delta=timedelta(0)):
        now["t"] += delta
        return now["t"]
    return advance


def _fail_until_blocked(throttle, key):
    for _ in range(throttle.limit - 1):
        assert throttle.fail(key) is None
    until = throttle.fail(key)
    assert until is not None
    return until


def test_blocks_after_limit_and_lifts_after_block_time(clock):
    t = Throttle(3, BLOCK)
    until = _fail_until_blocked(t, "ip")
    assert until == clock() + BLOCK
    assert t.blocked_until("ip") == until
    clock(BLOCK)
    assert t.blocked_until("ip") is None


def test_each_block_doubles_up_to_the_cap(clock):
    t = Throttle(3, BLOCK)
    lengths = []
    for _ in range(12):
        start = clock()
        until = _fail_until_blocked(t, "ip")
        lengths.append(until - start)
        clock(until - start)  # wait the block out
    assert lengths[:5] == [BLOCK * 2**i for i in range(5)]
    assert max(lengths) == MAX_BLOCK_TIME
    assert lengths[-1] == MAX_BLOCK_TIME


def test_success_forgets_failures_and_backoff(clock):
    t = Throttle(3, BLOCK)
    _fail_until_blocked(t, "ip")
    clock(BLOCK)
    t.reset("ip")
    assert _fail_until_blocked(t, "ip") == clock() + BLOCK  # back to the first block length


def test_keys_are_independent(clock):
    t = Throttle(2, BLOCK)
    _fail_until_blocked(t, "a")
    assert t.blocked_until("b") is None
    assert t.fail("b") is None


def test_a_quiet_day_forgets_a_client(clock):
    t = Throttle(3, BLOCK)
    _fail_until_blocked(t, "ip")
    clock(MAX_BLOCK_TIME + timedelta(hours=2))
    t.fail("other")  # any failure lets the throttle prune
    assert "ip" not in t._records
    assert _fail_until_blocked(t, "ip") == clock() + BLOCK


def test_sitting_out_the_longest_block_keeps_the_backoff(clock):
    t = Throttle(1, MAX_BLOCK_TIME / 2)
    t.fail("ip")                # 12h block; the next one will be 24h
    clock(MAX_BLOCK_TIME / 2)
    t.fail("ip")                # 24h block
    clock(MAX_BLOCK_TIME)       # sat out; the last failure is now a day old
    assert t.fail("ip") == clock() + MAX_BLOCK_TIME


def test_pruning_keeps_recent_clients(clock):
    t = Throttle(3, BLOCK)
    t.fail("ip")
    clock(timedelta(hours=2))
    t.fail("other")
    t.fail("ip")
    assert t.fail("ip") is not None  # the first failure still counted


def test_alarm_goes_off_once_per_hour(clock):
    alarm = FailureAlarm(3)
    assert [alarm.record() for _ in range(6)] == [False, False, True, False, False, False]
    clock(timedelta(hours=1))
    assert [alarm.record() for _ in range(3)] == [False, False, True]
