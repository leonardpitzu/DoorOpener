"""Security and rate-limiting tests."""
import json
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture
def app_module():
    import app as app_module
    return app_module


@pytest.fixture
def _client(app_module):
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        with app_module.app.app_context():
            yield c


def _std_headers():
    return {
        "User-Agent": "pytest-client/1.0 (+https://example.test) long-ua",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
    }


def test_security_headers_on_index(_client):
    resp = _client.get("/", headers={"User-Agent": "pytest", "Accept-Language": "en"})
    assert resp.status_code == 200
    for h in [
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Content-Security-Policy",
    ]:
        assert h in resp.headers
    # X-XSS-Protection is intentionally not set (superseded by CSP)
    assert "X-XSS-Protection" not in resp.headers


def _block(throttle, key):
    for _ in range(throttle.limit):
        throttle.fail(key)


def _session_id(client):
    client.post("/open-door", data=json.dumps({}), headers=_std_headers())
    with client.session_transaction() as s:
        return s["_session_id"]


def test_open_door_session_blocked_flow(_client, app_module):
    _block(app_module.rate_limiter.pin_session, _session_id(_client))
    r = _client.post(
        "/open-door", data=json.dumps({"pin": "1234"}), headers=_std_headers()
    )
    assert r.status_code == 429
    data = r.get_json()
    assert "blocked_until" in data


def test_open_door_ip_blocked_flow(_client, app_module, monkeypatch):
    monkeypatch.setattr("app.client_identity", lambda: ("9.9.9.9", "sessX"))
    _block(app_module.rate_limiter.pin_ip, "9.9.9.9")
    r = _client.post(
        "/open-door", data=json.dumps({"pin": "1234"}), headers=_std_headers()
    )
    assert r.status_code == 429


def test_admin_auth_blocking(_client, app_module, monkeypatch):
    import config
    wrong = {"password": "nope", "remember_me": False}
    h = _std_headers()
    for _ in range(config.SESSION_MAX_ATTEMPTS):
        r = _client.post("/admin/auth", data=json.dumps(wrong), headers=h)
        assert r.status_code == 403
    r = _client.post("/admin/auth", data=json.dumps(wrong), headers=h)
    assert r.status_code == 429


def test_admin_auth_success(_client, app_module, monkeypatch):
    import config
    monkeypatch.setattr(config, "admin_password", "secret")
    r = _client.post(
        "/admin/auth",
        data=json.dumps({"password": "secret", "remember_me": True}),
        headers=_std_headers(),
    )
    assert r.status_code == 200
    r2 = _client.get("/admin/check-auth")
    assert r2.status_code == 200
    data = r2.get_json()
    assert data.get("authenticated") is True


def test_testmode_pin_success(_client, app_module):
    app_module.users_store.create_user("alice", "1234")
    r = _client.post(
        "/open-door", data=json.dumps({"pin": "1234"}), headers=_std_headers()
    )
    assert r.status_code == 200
    assert "TEST MODE" in r.get_json().get("message", "")


def test_admin_logout_endpoint(_client):
    with _client.session_transaction() as s:
        s["admin_authenticated"] = True
        s["admin_login_time"] = datetime.now(timezone.utc).isoformat()
    r = _client.post("/admin/logout")
    assert r.status_code == 200
    r2 = _client.get("/admin/check-auth")
    assert r2.status_code == 200
    assert r2.get_json().get("authenticated") is False


def test_admin_page_renders(_client):
    r = _client.get("/admin")
    assert r.status_code == 200


def test_battery_non200_returns_none(_client, app_module):
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "error"
    with patch.object(app_module.ha_client.session, "get", return_value=mock_response):
        response = _client.get("/battery")
        assert response.status_code == 200
        assert response.get_json()["level"] is None


def test_battery_exception_returns_none(_client, app_module):
    with patch.object(app_module.ha_client.session, "get", side_effect=Exception("boom")):
        response = _client.get("/battery")
        assert response.status_code == 200
        assert response.get_json()["level"] is None


def test_open_door_success_switch(_client, app_module, monkeypatch):
    import config
    app_module.users_store.create_user("alice", "1234")
    monkeypatch.setattr(config, "entity_id", "switch.test_door")
    monkeypatch.setattr(config, "test_mode", False)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = lambda: None
    with patch.object(app_module.ha_client.session, "post", return_value=mock_resp):
        r = _client.post(
            "/open-door", data=json.dumps({"pin": "1234"}), headers=_std_headers()
        )
        assert r.status_code == 200
        msg = r.get_json().get("message", "")
        assert "Door open" in msg


def test_open_door_success_lock(_client, app_module, monkeypatch):
    import config
    app_module.users_store.create_user("alice", "1234")
    monkeypatch.setattr(config, "entity_id", "lock.test_door")
    monkeypatch.setattr(config, "test_mode", False)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = lambda: None
    with patch.object(app_module.ha_client.session, "post", return_value=mock_resp):
        r = _client.post(
            "/open-door", data=json.dumps({"pin": "1234"}), headers=_std_headers()
        )
        assert r.status_code == 200


def test_open_door_success_input_boolean(_client, app_module, monkeypatch):
    import config
    app_module.users_store.create_user("alice", "1234")
    monkeypatch.setattr(config, "entity_id", "input_boolean.open")
    monkeypatch.setattr(config, "test_mode", False)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = lambda: None
    with patch.object(app_module.ha_client.session, "post", return_value=mock_resp):
        r = _client.post(
            "/open-door", data=json.dumps({"pin": "1234"}), headers=_std_headers()
        )
        assert r.status_code == 200


def test_open_door_success_button(_client, app_module, monkeypatch):
    import config
    app_module.users_store.create_user("alice", "1234")
    monkeypatch.setattr(config, "entity_id", "button.gate_open")
    monkeypatch.setattr(config, "test_mode", False)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = lambda: None
    with patch.object(app_module.ha_client.session, "post", return_value=mock_resp):
        r = _client.post(
            "/open-door", data=json.dumps({"pin": "1234"}), headers=_std_headers()
        )
        assert r.status_code == 200


def test_battery_invalid_format(_client, app_module):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"state": "unknown"}
    with patch.object(app_module.ha_client.session, "get", return_value=mock_response):
        response = _client.get("/battery")
        assert response.status_code == 200
        assert response.get_json()["level"] is None


def test_admin_logs_parsing(_client, app_module):
    app_module.audit_log.clear()
    app_module.audit_log.record("1.2.3.4", "sess", "alice", "SUCCESS", "Door opened")
    with _client.session_transaction() as s:
        s["admin_authenticated"] = True
        s["admin_login_time"] = datetime.now(timezone.utc).isoformat()
    r = _client.get("/admin/logs")
    assert r.status_code == 200
    data = r.get_json()
    assert "logs" in data and isinstance(data["logs"], list)
    assert any(row.get("user") == "alice" for row in data["logs"])


def test_blocked_denies_correct_pin_and_returns_blocked_until(_client, app_module):
    app_module.users_store.create_user("alice", "1234")
    _block(app_module.rate_limiter.pin_session, _session_id(_client))
    r = _client.post(
        "/open-door", data=json.dumps({"pin": "1234"}), headers=_std_headers()
    )
    assert r.status_code == 429
    data = r.get_json()
    assert data.get("status") == "error"
    assert "blocked_until" in data


def test_open_door_block_set_on_failure_includes_blocked_until(_client, app_module):
    import config
    headers = _std_headers()
    _client.post("/open-door", data=json.dumps({"pin": "0000"}), headers=headers)
    with _client.session_transaction() as s:
        sid = s.get("_session_id")
    assert sid
    for i in range(config.SESSION_MAX_ATTEMPTS - 1):
        r = _client.post("/open-door", data=json.dumps({"pin": "0000"}), headers=headers)
    assert r.status_code == 401
    data = r.get_json()
    assert data.get("status") == "error"
    assert "blocked_until" in data
    r2 = _client.post("/open-door", data=json.dumps({"pin": "0000"}), headers=headers)
    assert r2.status_code == 429
    data2 = r2.get_json()
    assert "blocked_until" in data2


def test_invalid_pin_format_counts_toward_a_block(_client, app_module):
    """Bad-format PINs are failures: the one that trips the block says so, the next gets 429."""
    import config
    headers = _std_headers()
    for _ in range(config.SESSION_MAX_ATTEMPTS - 1):
        r = _client.post("/open-door", data=json.dumps({"pin": "abc"}), headers=headers)
        assert r.status_code == 400
        assert "blocked_until" not in r.get_json()
    r = _client.post("/open-door", data=json.dumps({"pin": "abc"}), headers=headers)
    assert r.status_code == 400
    assert "blocked_until" in r.get_json()
    r = _client.post("/open-door", data=json.dumps({"pin": "abc"}), headers=headers)
    assert r.status_code == 429


def test_admin_ip_block_across_sessions(_client, app_module, monkeypatch):
    """Same IP, rotating sessions: the per-IP admin throttle still trips."""
    counter = {"n": 0}

    def fake_identity():
        counter["n"] += 1
        return "5.5.5.5", f"sess-{counter['n']}"

    monkeypatch.setattr("app.client_identity", fake_identity)
    wrong = json.dumps({"password": "nope", "remember_me": False})
    h = _std_headers()
    for _ in range(app_module.config.SESSION_MAX_ATTEMPTS):
        r = _client.post("/admin/auth", data=wrong, headers=h)
        assert r.status_code == 403
    r = _client.post("/admin/auth", data=wrong, headers=h)
    assert r.status_code == 429
    assert "blocked_until" in r.get_json()

