"""Keypad brute-force protection, end to end.

Each test pins down a way the protection used to be bypassable or abusable.
"""
import itertools
from unittest.mock import MagicMock, patch

import pytest

import config


@pytest.fixture
def app_module():
    import app as app_module
    app_module.users_store.create_user("alice", "1357")
    app_module.users_store.create_user("bob", "2468", active=False)
    return app_module


def _fresh_client(app_module):
    """A client with no cookies, like an attacker who drops them after every try."""
    return app_module.app.test_client()


def _try(client, pin, **headers):
    return client.post("/open-door", json={"pin": pin}, headers=headers)


def test_forged_x_forwarded_for_does_not_dodge_the_ip_block(app_module):
    # trusted_proxies defaults to 0: the header is ignored and the socket address counts.
    codes = [_try(_fresh_client(app_module), "9999", **{"X-Forwarded-For": f"10.0.{i}.1"}).status_code
             for i in range(config.MAX_ATTEMPTS + 1)]
    assert codes[-1] == 429


def test_rotating_headers_does_not_dodge_the_ip_block(app_module):
    agents = (f"Mozilla/5.0 build {i}" for i in itertools.count())
    codes = [_try(_fresh_client(app_module), "9999", **{"User-Agent": next(agents), "Accept-Language": "x"}).status_code
             for _ in range(config.MAX_ATTEMPTS + 1)]
    assert codes[-1] == 429


@pytest.mark.parametrize("agent", [
    "curl/8.5.0",
    "HomeAssistant/2026.11 aiohttp/3.11",
    "Mozilla/5.0 (Linux; Android 12; CUBOT KINGKONG 7) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36",
    "",
])
def test_any_user_agent_may_open_the_door(app_module, agent):
    assert _try(_fresh_client(app_module), "1357", **{"User-Agent": agent}).status_code == 200


def test_a_disabled_pin_is_just_a_wrong_pin(app_module):
    client = _fresh_client(app_module)
    responses = [_try(client, "2468") for _ in range(config.SESSION_MAX_ATTEMPTS)]

    assert [r.status_code for r in responses] == [401] * config.SESSION_MAX_ATTEMPTS
    assert responses[0].get_json()["message"] == _try(_fresh_client(app_module), "0000").get_json()["message"]
    assert "blocked_until" in responses[-1].get_json()  # it counted
    assert _try(client, "2468").status_code == 429
    entries = app_module.audit_log.tail()
    assert any(e["status"] == "DISABLED" and e["user"] == "bob" for e in entries)


def test_unicode_digits_are_an_invalid_format_not_a_crash(app_module):
    r = _try(_fresh_client(app_module), "١٣٥٧")  # Arabic-Indic 1357
    assert r.status_code == 400
    assert r.get_json()["message"] == "Invalid PIN format"


def test_wrong_pins_do_not_reveal_how_many_tries_are_left(app_module):
    assert _try(_fresh_client(app_module), "0000").get_json()["message"] == "Invalid PIN"


def test_the_global_threshold_alerts_but_never_locks_anyone_out(app_module, monkeypatch):
    monkeypatch.setattr(config, "test_mode", False)
    monkeypatch.setattr(app_module, "_in_background", lambda fn, *args: fn(*args))
    notify = MagicMock()
    monkeypatch.setattr(type(app_module.ha_client), "notify", notify)  # __slots__: patch the class
    ips = (f"10.9.{i // 250}.{i % 250}" for i in itertools.count())
    monkeypatch.setattr("app.client_identity", lambda: (next(ips), "s"))

    for _ in range(config.MAX_GLOBAL_ATTEMPTS_PER_HOUR + 5):
        app_module.rate_limiter.pin_session.reset("s")
        _try(_fresh_client(app_module), "0000")

    notify.assert_called_once()
    assert "possible brute force" in notify.call_args.args[0]
    assert [e["status"] for e in app_module.audit_log.tail()].count("GLOBAL_THRESHOLD") >= 1

    ok = MagicMock(status_code=200, raise_for_status=lambda: None)
    with patch.object(app_module.ha_client.session, "post", return_value=ok):
        assert _try(_fresh_client(app_module), "1357").status_code == 200


def test_admin_failures_count_toward_the_alert(app_module, monkeypatch):
    ips = (f"10.8.{i // 250}.{i % 250}" for i in itertools.count())
    monkeypatch.setattr("app.client_identity", lambda: (next(ips), "s"))
    alarm = app_module.rate_limiter.alarm
    for _ in range(alarm.threshold - 1):
        _fresh_client(app_module).post("/admin/auth", json={"password": "nope"})
    assert alarm.record() is True  # one more failure reaches the threshold


def test_home_assistant_failures_reach_the_audit_log(app_module, monkeypatch):
    import requests

    monkeypatch.setattr(config, "test_mode", False)
    with patch.object(app_module.ha_client.session, "post", side_effect=requests.ConnectionError("http://ha:8123 down")):
        r = _try(_fresh_client(app_module), "1357")
    assert r.status_code == 502
    entry = app_module.audit_log.tail()[-1]
    assert entry["status"] == "FAILURE" and entry["user"] == "alice"
    assert entry["details"] == "Home Assistant unreachable (ConnectionError)"  # no URL leaked

    denied = MagicMock(status_code=401)
    denied.raise_for_status.side_effect = requests.HTTPError(response=denied)
    with patch.object(app_module.ha_client.session, "post", return_value=denied):
        _try(_fresh_client(app_module), "1357")
    assert app_module.audit_log.tail()[-1]["details"] == "Home Assistant API error: 401"


def test_a_failed_notification_never_raises(app_module, caplog):
    import requests

    with patch.object(app_module.ha_client.session, "post", side_effect=requests.Timeout("slow")):
        app_module.ha_client.notify("title", "message")  # must not raise
    assert "Could not send Home Assistant notification" in caplog.text


def test_an_unreadable_user_store_is_a_503_and_changes_nothing(app_module, monkeypatch):
    from users_store import UsersStore, UsersStoreError

    def unreadable(self, pin):
        raise UsersStoreError("Cannot parse users store")

    monkeypatch.setattr(UsersStore, "lookup_pin", unreadable)
    r = _try(_fresh_client(app_module), "1357")
    assert r.status_code == 503
    assert r.get_json()["status"] == "error"
