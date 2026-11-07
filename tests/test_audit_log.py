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
