"""Additional integration tests: battery edge cases and admin log parsing."""
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock


def client_app():
    from app import app as flask_app
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def test_battery_out_of_range_and_none_paths():
    import app as app_module

    # Out of range value
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"state": "150"}
    with patch.object(app_module.ha_client.session, "get", return_value=mock_response):
        c = client_app()
        response = c.get("/battery")
        assert response.get_json()["level"] is None

    # None value
    mock_response2 = MagicMock()
    mock_response2.status_code = 200
    mock_response2.json.return_value = {"state": None}
    with patch.object(app_module.ha_client.session, "get", return_value=mock_response2):
        c = client_app()
        response2 = c.get("/battery")
        assert response2.get_json()["level"] is None


def test_admin_logs_old_format_parsing():
    import app as app_module

    app_module.audit_log.clear()
    with open(app_module.audit_log.path, "a", encoding="utf-8") as f:
        f.write("2025-09-01T12:00:00Z - 1.2.3.4 - alice - SUCCESS - Door opened\n")
    c = client_app()
    with c.session_transaction() as s:
        s["admin_authenticated"] = True
        s["admin_login_time"] = datetime.now(timezone.utc).isoformat()
    r = c.get("/admin/logs")
    assert r.status_code == 200
    assert r.get_json()["logs"] == [{
        "timestamp": "2025-09-01T12:00:00Z",
        "ip": "1.2.3.4",
        "user": "alice",
        "status": "SUCCESS",
        "details": "Door opened",
    }]
