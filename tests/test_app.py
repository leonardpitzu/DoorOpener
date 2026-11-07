"""Core app tests: utility functions, basic routes, and error paths."""
from datetime import datetime
from unittest.mock import MagicMock, patch


def test_get_current_time():
    from config import get_current_time
    assert isinstance(get_current_time(), datetime)


def test_index_route(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Door" in response.data


def test_battery_route(client):
    import app as app_module
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"state": "85"}
    with patch.object(app_module.ha_client.session, "get", return_value=mock_response):
        response = client.get("/battery")
        assert response.status_code == 200
        assert response.get_json()["level"] == 85


def test_battery_route_disabled(client, monkeypatch):
    """A door without a battery sensor gets null, without calling HA."""
    import app as app_module
    import config
    door = config.Door("door", "Door", "switch.test_door")
    monkeypatch.setattr(config, "doors", (door,))
    monkeypatch.setattr(config, "_doors_by_id", {"door": door})
    get = MagicMock()
    monkeypatch.setattr(app_module.ha_client.session, "get", get)
    for path in ("/battery", "/battery?door=door", "/battery?door=nope"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.get_json()["level"] is None
    assert get.call_count == 0


def test_open_door_invalid_input(client):
    headers = {
        "User-Agent": "pytest-client/1.0 (+https://example.test)",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
    }
    response = client.post("/open-door", json={}, headers=headers)
    assert response.status_code == 400

    response = client.post("/open-door", json={"pin": "abc"}, headers=headers)
    assert response.status_code == 400


def test_admin_authentication(client):
    response = client.get("/admin/logs")
    assert response.status_code == 401
