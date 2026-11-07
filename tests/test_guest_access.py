"""Guest PINs at the keypad, and what Home Assistant hears about each opening."""
from datetime import datetime
from unittest.mock import MagicMock

import pytest

import config
from ha_client import DOOR_OPENED_EVENT

WED_NOON = datetime(2026, 11, 11, 12, 0, tzinfo=config.TIMEZONE)


@pytest.fixture
def app_module(monkeypatch):
    import app
    monkeypatch.setattr(app, "get_current_time", lambda: WED_NOON)
    return app


def _open(app_module, pin):
    return app_module.app.test_client().post("/open-door", json={"pin": pin})


def _last_audit(app_module):
    return app_module.audit_log.tail()[-1]


@pytest.mark.parametrize("rules, status", [
    ({"valid_until": "2026-11-10T18:00"}, "EXPIRED"),
    ({"valid_from": "2026-11-12T08:00"}, "NOT_YET_VALID"),
    ({"days": [5, 6]}, "OUTSIDE_SCHEDULE"),
    ({"hours": ["14:00", "18:00"]}, "OUTSIDE_SCHEDULE"),
])
def test_a_pin_outside_its_window_is_just_a_wrong_pin(app_module, rules, status):
    app_module.users_store.create_user("guest", "2468", access_rules=rules)
    r = _open(app_module, "2468")
    assert r.status_code == 401
    assert r.get_json()["message"] == "Invalid PIN"  # nothing tells a stranger the PIN exists
    entry = _last_audit(app_module)
    assert (entry["user"], entry["status"]) == ("guest", status)


def test_a_pin_inside_its_window_opens(app_module):
    app_module.users_store.create_user(
        "cleaner", "2468", access_rules={"days": [0, 1, 2, 3, 4], "hours": ["08:00", "18:00"]})
    assert _open(app_module, "2468").status_code == 200


def test_a_one_time_pin_opens_once(app_module):
    app_module.users_store.create_user("courier", "8642", access_rules={"one_time": True})
    assert _open(app_module, "8642").status_code == 200
    assert "one-time PIN used up" in _last_audit(app_module)["details"]
    assert _open(app_module, "8642").status_code == 401
    assert _last_audit(app_module)["status"] == "DISABLED"


def test_access_rules_through_the_admin_api(client, app_module):
    with client.session_transaction() as s:
        s["admin_authenticated"] = True
        s["admin_login_time"] = WED_NOON.isoformat()
    rules = {"valid_until": "2026-11-12T18:00", "one_time": True}
    assert client.post("/admin/users", json={"username": "guest", "pin": "2468", "access": rules}).status_code == 201
    listed = client.get("/admin/users").get_json()["users"][0]["access"]
    assert listed["valid_until"] == "2026-11-12T18:00" and listed["one_time"] is True

    bad = client.put("/admin/users/guest", json={"access": {"hours": ["08:00"]}})
    assert bad.status_code == 400 and "hours" in bad.get_json()["message"]
    assert client.put("/admin/users/guest", json={"pin": "1357"}).status_code == 200  # rules kept
    assert client.get("/admin/users").get_json()["users"][0]["access"]["one_time"] is True
    assert client.put("/admin/users/guest", json={"access": None}).status_code == 200  # rules cleared
    assert client.get("/admin/users").get_json()["users"][0]["access"] is None


# ---------------------------------------------------------------------------
# Home Assistant hears who came in
# ---------------------------------------------------------------------------
@pytest.fixture
def posts(app_module, monkeypatch):
    """Every POST to Home Assistant, with background calls run inline."""
    monkeypatch.setattr(config, "test_mode", False)
    monkeypatch.setattr(app_module, "_in_background", lambda fn, *args: fn(*args))
    post = MagicMock(return_value=MagicMock(status_code=200, raise_for_status=lambda: None))
    monkeypatch.setattr(app_module.ha_client.session, "post", post)
    return post


def _paths(post):
    return [call.args[0].removeprefix(config.ha_url) for call in post.call_args_list]


def test_an_opening_is_an_event_and_a_logbook_entry(app_module, posts):
    app_module.users_store.create_user("alice", "1357")
    assert _open(app_module, "1357").status_code == 200
    assert _paths(posts) == ["/api/services/switch/turn_on", f"/api/events/{DOOR_OPENED_EVENT}",
                             "/api/services/logbook/log"]
    assert posts.call_args_list[1].kwargs["json"] == {"user": "alice", "entity_id": config.entity_id}
    assert posts.call_args_list[2].kwargs["json"]["message"] == "opened by alice"


def test_a_failing_event_does_not_fail_the_opening(app_module, posts):
    import requests
    app_module.users_store.create_user("alice", "1357")
    posts.side_effect = [MagicMock(raise_for_status=lambda: None), requests.ConnectionError("down"),
                         requests.ConnectionError("down")]
    assert _open(app_module, "1357").status_code == 200


def test_test_mode_tells_home_assistant_nothing(app_module, posts, monkeypatch):
    monkeypatch.setattr(config, "test_mode", True)
    app_module.users_store.create_user("alice", "1357")
    assert _open(app_module, "1357").status_code == 200
    assert posts.call_count == 0


# ---------------------------------------------------------------------------
# No Home Assistant at all: everything else keeps working
# ---------------------------------------------------------------------------
@pytest.fixture
def no_home_assistant(app_module, monkeypatch):
    monkeypatch.setattr(config, "ha_token", "")
    monkeypatch.setattr(config, "test_mode", False)
    post = MagicMock()
    monkeypatch.setattr(app_module.ha_client.session, "post", post)
    monkeypatch.setattr(app_module.ha_client.session, "get", post)
    return post


def test_without_home_assistant_the_keypad_answers_and_calls_nobody(app_module, no_home_assistant, caplog):
    app_module.users_store.create_user("alice", "1357")
    r = _open(app_module, "1357")
    assert r.status_code == 502 and r.get_json()["status"] == "error"
    assert _last_audit(app_module)["details"] == "No Home Assistant configured"
    assert _open(app_module, "9999").status_code == 401
    assert app_module.app.test_client().get("/battery").get_json() == {"level": None}
    app_module.ha_client.notify("t", "m")
    app_module.ha_client.door_opened("alice")
    app_module.ha_client.check_connection()
    assert no_home_assistant.call_count == 0
    assert "No Home Assistant configured" in caplog.text


@pytest.mark.parametrize("status, logged", [(200, "Connected to Home Assistant"),
                                             (401, "refused the access token"),
                                             (500, "API error: 500")])
def test_the_startup_check_says_what_home_assistant_answered(app_module, monkeypatch, caplog, status, logged):
    import logging
    monkeypatch.setattr(app_module.ha_client.session, "get", MagicMock(return_value=MagicMock(status_code=status)))
    with caplog.at_level(logging.INFO, logger="dooropener"):
        app_module.ha_client.check_connection()
    assert logged in caplog.text
