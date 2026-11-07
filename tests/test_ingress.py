"""The admin panel through Home Assistant's ingress, and the public port without one."""
import hashlib
import hmac
import re
import socket
import urllib.error
import urllib.request

import pytest
from werkzeug.test import Client

import ingress

PREFIX = "/api/hassio_ingress/tok_9f3A-x"
SUPERVISOR = {"REMOTE_ADDR": ingress.SUPERVISOR_IP}
ADMIN = {"X-Ingress-Path": PREFIX, "X-Remote-User-Groups": "system-users,system-admin",
         "X-Remote-User-Name": "leo", "X-Remote-User-Display-Name": "Leo"}
NOT_ADMIN = {**ADMIN, "X-Remote-User-Groups": "system-users"}


@pytest.fixture
def ha(app_module):
    """A client that talks to DoorOpener the way the Supervisor does."""
    return Client(ingress.IngressApp(app_module.app))


@pytest.fixture
def app_module():
    import app
    return app


def _get(client, path, headers=ADMIN, **kw):
    return client.get(path, environ_base=SUPERVISOR, headers=headers, **kw)


def test_only_the_supervisor_may_connect(ha):
    r = ha.get("/", environ_base={"REMOTE_ADDR": "172.30.33.9"}, headers=ADMIN)
    assert r.status_code == 403


def test_the_sidebar_opens_the_admin_panel_without_a_password(ha):
    r = _get(ha, "/")
    page = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "Signed in through Home Assistant as Leo" in page
    assert 'id="loginForm"' not in page and 'id="logoutBtn"' not in page
    assert f'data-base="{PREFIX}"' in page
    assert f'{PREFIX}/static/admin.js?v=' in page  # every URL carries the ingress path


def test_ingress_pages_may_be_framed_by_home_assistant_only(ha, client):
    headers = _get(ha, "/").headers
    assert headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in headers["Content-Security-Policy"]
    public = client.get("/admin").headers
    assert public["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in public["Content-Security-Policy"]


def test_ingress_sets_no_cookies(ha):
    for path in ("/", "/admin", "/admin/users", "/admin/logs"):
        assert "Set-Cookie" not in _get(ha, path).headers, path


def test_home_assistant_administrators_only(ha):
    assert _get(ha, "/", headers=NOT_ADMIN).status_code == 403
    assert _get(ha, "/admin/users", headers=NOT_ADMIN).status_code == 403
    no_user = {"X-Ingress-Path": PREFIX}  # the Supervisor could not say who it is
    assert _get(ha, "/admin/users", headers=no_user).status_code == 403


def test_the_admin_api_works_through_ingress_with_its_csrf_token(ha, app_module, monkeypatch):
    monkeypatch.setitem(app_module.app.config, "CSRF_ENABLED", True)
    page = _get(ha, "/").get_data(as_text=True)
    token = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
    assert token == hmac.new(app_module.app.secret_key.encode(), PREFIX.encode(), hashlib.sha256).hexdigest()

    body = {"username": "guest", "pin": "2468"}
    refused = ha.post("/admin/users", environ_base=SUPERVISOR, headers=ADMIN, json=body)
    assert refused.status_code == 403
    created = ha.post("/admin/users", environ_base=SUPERVISOR, headers={**ADMIN, "X-CSRF-Token": token}, json=body)
    assert created.status_code == 201
    other_session = {**ADMIN, "X-Ingress-Path": "/api/hassio_ingress/another", "X-CSRF-Token": token}
    assert ha.delete("/admin/users/guest", environ_base=SUPERVISOR, headers=other_session).status_code == 403


def test_admin_actions_through_ingress_name_the_home_assistant_user(ha, app_module):
    ha.post("/admin/users", environ_base=SUPERVISOR, headers=ADMIN, json={"username": "guest", "pin": "2468"})
    entry = app_module.audit_log.tail()[-1]
    assert entry["user"] == "ADMIN (Leo)"
    assert entry["status"] == "ADMIN_USER_CREATE"


def test_the_password_login_does_not_exist_through_ingress(ha):
    r = ha.post("/admin/auth", environ_base=SUPERVISOR, headers=ADMIN, json={"password": "testpass"})
    assert r.status_code == 404


def test_a_forged_ingress_path_is_not_used(ha):
    page = _get(ha, "/", headers={**ADMIN, "X-Ingress-Path": "https://evil.example/x"}).get_data(as_text=True)
    assert 'data-base=""' in page


# ---------------------------------------------------------------------------
# The public port of an app without an admin password
# ---------------------------------------------------------------------------
@pytest.fixture
def no_public_admin(app_module, monkeypatch):
    import config
    monkeypatch.setattr(config, "public_admin", False)
    monkeypatch.setattr(config, "admin_password", "")


@pytest.mark.usefixtures("no_public_admin")
def test_without_a_password_the_admin_panel_is_only_in_home_assistant(client, ha):
    page = client.get("/admin")
    assert page.status_code == 404
    assert "open DoorOpener in its sidebar" in page.get_data(as_text=True)
    assert client.get("/admin/users").status_code == 404
    assert client.post("/admin/auth", json={"password": ""}).status_code == 404
    assert "gear-spin-wrap" not in client.get("/").get_data(as_text=True)
    assert _get(ha, "/admin/users").status_code == 200  # Home Assistant still gets in


def test_with_a_password_the_keypad_links_the_admin_panel(client):
    assert "gear-spin-wrap" in client.get("/").get_data(as_text=True)


# ---------------------------------------------------------------------------
# The listener itself
# ---------------------------------------------------------------------------
def test_the_listener_serves_in_the_background_and_refuses_strangers(app_module):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = ingress.serve(app_module.app, port=port)
    try:
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5)  # nosec B310 - local test server
        assert refused.value.code == 403  # 127.0.0.1 is not the Supervisor
    finally:
        server.shutdown()
