"""The audit log: recording, reading back, and clearing without losing later entries."""
import pytest

from audit import AuditLog


@pytest.fixture
def log(tmp_path):
    return AuditLog(str(tmp_path / "logs" / "log.txt"))


def _statuses(log):
    return [entry["status"] for entry in log.tail()]


def test_records_and_reads_back(log):
    log.record("1.2.3.4", "abcdefghij", "alice", "SUCCESS", "Door opened")
    assert log.tail() == [{
        "timestamp": log.tail()[0]["timestamp"],
        "ip": "1.2.3.4",
        "user": "alice",
        "status": "SUCCESS",
        "details": "Door opened",
    }]


def test_unknown_user_reads_back_as_none(log):
    log.record("1.2.3.4", None, "UNKNOWN", "AUTH_FAILURE", "Invalid PIN")
    assert log.tail()[0]["user"] is None


def test_tail_keeps_the_newest_entries(log):
    for i in range(30):
        log.record("ip", "s", "u", f"S{i}", "d")
    assert _statuses(log)[-1] == "S29"
    assert [e["status"] for e in log.tail(limit=3)] == ["S27", "S28", "S29"]


def test_tail_skips_lines_that_are_not_entries(log):
    log.record("ip", "s", "u", "GOOD", "d")
    with open(log.path, "a", encoding="utf-8") as f:
        f.write("garbage\n{not json\n")
    assert _statuses(log) == ["GOOD"]


@pytest.mark.parametrize("test_only", [False, True])
def test_logging_carries_on_after_a_clear(log, test_only):
    log.record("ip", "s", "u", "SUCCESS", "Door opened (TEST MODE)")
    log.clear(test_only=test_only)
    log.record("ip", "s", "u", "AFTER", "written after the clear")
    assert _statuses(log) == ["AFTER"]


def test_clear_test_only_keeps_real_entries(log):
    log.record("ip", "s", "u", "SUCCESS", "Door opened (TEST MODE)")
    log.record("ip", "s", "u", "SUCCESS", "Door opened")
    log.record("ip", "s", "u", "AUTH_FAILURE", "Invalid PIN")
    assert log.clear(test_only=True) == (1, 2)
    assert [e["details"] for e in log.tail()] == ["Door opened", "Invalid PIN"]


def test_clear_all(log):
    log.record("ip", "s", "u", "SUCCESS", "Door opened")
    assert log.clear() == (1, 0)
    assert log.tail() == []


def test_clearing_a_missing_log_is_harmless(log):
    import os
    os.remove(log.path)
    assert log.clear(test_only=True) == (0, 0)
    assert log.tail() == []


def test_admin_clear_endpoint_keeps_logging(client):
    import app as app_module
    from datetime import datetime, timezone

    with client.session_transaction() as s:
        s["admin_authenticated"] = True
        s["admin_login_time"] = datetime.now(timezone.utc).isoformat()
    r = client.post("/admin/logs/clear", json={"mode": "test_only"})
    assert r.status_code == 200
    assert app_module.audit_log.tail()[-1]["status"] == "ADMIN_LOGS_CLEAR"  # its own entry survived
    assert client.post("/admin/logs/clear", json={"mode": "bogus"}).status_code == 400


# ---------------------------------------------------------------------------
# A circular log: 180 days at most, and a size cap
# ---------------------------------------------------------------------------
def _entry(when, status="SUCCESS"):
    import json
    return json.dumps({"timestamp": when.isoformat(), "ip": "ip", "session": "s", "user": "u",
                       "status": status, "details": "d"}) + "\n"


def test_entries_older_than_180_days_are_dropped(tmp_path):
    from datetime import timedelta
    from config import get_current_time
    path = tmp_path / "log.txt"
    now = get_current_time()
    path.write_text(_entry(now - timedelta(days=181), "OLD") + _entry(now - timedelta(days=179), "KEPT"))
    log = AuditLog(str(path))
    assert _statuses(log) == ["KEPT"]
    log.record("ip", "s", "u", "NEW", "d")
    assert _statuses(log) == ["KEPT", "NEW"]


def test_a_full_log_drops_its_oldest_entries(tmp_path):
    log = AuditLog(str(tmp_path / "log.txt"), max_bytes=20_000)
    for i in range(400):
        log.record("ip", "s", "u", f"S{i}", "d")
    assert (tmp_path / "log.txt").stat().st_size <= 20_000
    statuses = [e["status"] for e in log.tail(limit=1000)]
    assert statuses[-1] == "S399" and statuses[0] != "S0"
    assert statuses == [f"S{i}" for i in range(400 - len(statuses), 400)]  # oldest gone, nothing skipped


def test_rotated_logs_from_before_3_0_are_folded_back_in(tmp_path):
    from config import get_current_time
    path = tmp_path / "log.txt"
    now = get_current_time()
    (tmp_path / "log.txt.2").write_text("2026-11-01 10:00:00,000 - " + _entry(now, "OLDEST"))
    (tmp_path / "log.txt.1").write_text(_entry(now, "OLDER"))
    path.write_text(_entry(now, "NEWEST"))
    log = AuditLog(str(path))
    assert _statuses(log) == ["OLDEST", "OLDER", "NEWEST"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["log.txt"]


def test_trimming_keeps_the_logs_permissions(tmp_path):
    import os
    path = tmp_path / "log.txt"
    path.write_text("garbage\n")
    os.chmod(path, 0o644)
    AuditLog(str(path))  # drops the garbage line, rewriting the file
    assert path.read_text() == ""
    assert path.stat().st_mode & 0o777 == 0o644
