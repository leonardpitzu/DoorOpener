"""Admin sessions: login, server-side expiry, and the PIN generator behind them."""
from datetime import datetime, timedelta

import pytest

import config
from config import get_current_time


@pytest.fixture
def client():
    import app as app_module
    with app_module.app.test_client() as c:
        yield c


def _login_as_of(client, age: timedelta, *, remember: bool = False):
    with client.session_transaction() as s:
        s["admin_authenticated"] = True
        s["admin_login_time"] = (get_current_time() - age).isoformat()
        s.permanent = remember


@pytest.mark.parametrize("age, remember, allowed", [
    (timedelta(hours=11), False, True),
    (timedelta(hours=13), False, False),
    (timedelta(days=29), True, True),
    (timedelta(days=31), True, False),
    (timedelta(hours=-2), False, False),  # signed in the future: not trusted
])
def test_admin_sessions_expire_on_the_server(client, age, remember, allowed):
    _login_as_of(client, age, remember=remember)
    assert (client.get("/admin/users").status_code == 200) is allowed
    assert client.get("/admin/check-auth").get_json()["authenticated"] is allowed


def test_an_expired_session_is_cleared(client):
    _login_as_of(client, config.ADMIN_SESSION_MAX_AGE + timedelta(minutes=1))
    client.get("/admin/check-auth")
    with client.session_transaction() as s:
        assert "admin_authenticated" not in s


@pytest.mark.parametrize("login_time", [None, "yesterday", "2026-01-01T00:00:00"])  # missing, junk, naive
def test_a_session_without_a_usable_login_time_is_rejected(client, login_time):
    with client.session_transaction() as s:
        s["admin_authenticated"] = True
        if login_time:
            s["admin_login_time"] = login_time
    assert client.get("/admin/users").status_code == 401


def test_login_sets_the_server_checked_login_time(client):
    r = client.post("/admin/auth", json={"password": "testpass-0123", "remember_me": True})
    assert r.status_code == 200
    with client.session_transaction() as s:
        assert s.permanent
        age = get_current_time() - datetime.fromisoformat(s["admin_login_time"])
    assert timedelta(0) <= age < timedelta(minutes=1)


def test_a_non_ascii_admin_password_works(client, monkeypatch):
    monkeypatch.setattr(config, "admin_password", "türschloß-2026")
    assert client.post("/admin/auth", json={"password": "türschloß-2026"}).status_code == 200
    assert client.post("/admin/auth", json={"password": "türschloss-2026"}).status_code == 403


def test_a_hashed_admin_password_logs_in(client, monkeypatch):
    import pins
    monkeypatch.setattr(config, "admin_password", pins.hash_password("a-long-admin-password"))
    assert client.post("/admin/auth", json={"password": "a-long-admin-password"}).status_code == 200
    assert client.post("/admin/auth", json={"password": "a-long-admin-passwore"}).status_code == 403


def test_a_non_string_password_is_just_wrong(client):
    assert client.post("/admin/auth", json={"password": ["testpass-0123"]}).status_code == 403


def test_generate_pin_requires_admin(client):
    assert client.get("/admin/generate-pin").status_code == 401


def test_generate_pin_returns_an_unused_pin(client):
    import app as app_module
    from pins import is_valid_pin, trivial_pins

    app_module.users_store.create_user("alice", "482915", doors=["door"])
    _login_as_of(client, timedelta(0))
    for length in range(4, 9):
        r = client.get(f"/admin/generate-pin?length={length}")
        pin = r.get_json()["pin"]
        assert r.status_code == 200 and is_valid_pin(pin) and len(pin) == length
        assert pin != "482915" and pin not in trivial_pins(length)
    assert len(client.get("/admin/generate-pin").get_json()["pin"]) == 6  # the default


@pytest.mark.parametrize("length", ["3", "9", "six", ""])
def test_generate_pin_rejects_bad_lengths(client, length):
    _login_as_of(client, timedelta(0))
    r = client.get(f"/admin/generate-pin?length={length}")
    assert r.status_code == 400
    assert r.get_json()["status"] == "error"


def test_generate_pin_reports_a_full_space(client, monkeypatch):
    from users_store import UsersStore
    monkeypatch.setattr(UsersStore, "pin_taken", lambda self, pin, exclude=None: True)
    _login_as_of(client, timedelta(0))
    assert client.get("/admin/generate-pin?length=4").status_code == 409


def test_generated_pins_avoid_disabled_users(client, monkeypatch):
    import app as app_module
    import pins
    app_module.users_store.create_user("bob", "4821", active=False, doors=["door"])
    draws = iter([4821, 5093])
    monkeypatch.setattr(pins.secrets, "randbelow", lambda _n: next(draws))
    _login_as_of(client, timedelta(0))
    assert client.get("/admin/generate-pin?length=4").get_json()["pin"] == "5093"
