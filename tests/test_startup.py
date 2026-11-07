"""Startup configuration, add-on plumbing, and the small endpoints around them.

The startup checks run ``config.py`` in a subprocess with its own options file,
since they decide whether the module imports at all.
"""
import json
import os
import subprocess  # nosec B404 - runs this repo's own code with fixed arguments
import sys

import pytest
import trustme

import config

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dooropener"))
GOOD = {"admin_password": "a-real-password", "entity_id": "switch.door"}


def _import(tmp_path, code="import config", env=None, **options):
    opts = tmp_path / "options.json"
    opts.write_text(json.dumps({**GOOD, **options}))
    run_env = {k: v for k, v in os.environ.items() if k not in ("FLASK_SECRET_KEY", "SUPERVISOR_TOKEN")}
    run_env.update(DOOROPENER_OPTIONS_PATH=str(opts), DOOROPENER_LOG_DIR=str(tmp_path / "logs"),
                   USERS_STORE_PATH=str(tmp_path / "users.json"), **(env or {}))
    return subprocess.run(  # nosec B603
        [sys.executable, "-c", code], cwd=APP_DIR, env=run_env, capture_output=True, text=True, timeout=60
    )


def test_a_real_password_starts(tmp_path):
    assert _import(tmp_path).returncode == 0


@pytest.mark.parametrize("password", ["", "   ", "admin", "Password", "change_me_please"])
def test_refuses_to_start_with_a_guessable_admin_password(tmp_path, password):
    result = _import(tmp_path, admin_password=password)
    assert result.returncode != 0
    assert "admin_password" in result.stderr


@pytest.mark.parametrize("key", ["short", "change-me-to-something-random", "your-secret-key-here"])
def test_refuses_to_start_with_a_guessable_secret_key(tmp_path, key):
    result = _import(tmp_path, secret_key=key)
    assert result.returncode != 0
    assert "secret_key" in result.stderr


def test_the_secret_key_from_the_environment_is_checked_too(tmp_path):
    assert _import(tmp_path, env={"FLASK_SECRET_KEY": "dev"}).returncode != 0


def test_an_empty_secret_key_means_a_random_one(tmp_path):
    result = _import(tmp_path, "import config; print(config.secret_key, config.random_secret_warning)")
    assert result.stdout.split() == ["None", "True"]


@pytest.mark.parametrize("proxies, wrapped", [(0, False), (1, True)])
def test_x_forwarded_for_is_trusted_only_when_configured(tmp_path, proxies, wrapped):
    code = ("import app; from werkzeug.middleware.proxy_fix import ProxyFix; "
            "print(isinstance(app.app.wsgi_app, ProxyFix))")
    result = _import(tmp_path, code, trusted_proxies=proxies)
    assert result.stdout.strip() == str(wrapped), result.stderr


def _issue(directory, certfile="fullchain.pem", keyfile="privkey.pem", key_from=None):
    """A certificate and key in directory; key_from takes the key from another certificate."""
    leaf = trustme.CA().issue_cert("door.test")
    leaf.cert_chain_pems[0].write_to_path(str(directory / certfile))
    (key_from or leaf).private_key_pem.write_to_path(str(directory / keyfile))


def test_an_app_may_leave_the_admin_password_empty(tmp_path):
    code = "import config; print(config.ha_app, config.public_admin)"
    result = _import(tmp_path, code, env={"SUPERVISOR_TOKEN": "sup"}, admin_password="")
    assert result.stdout.split() == ["True", "False"], result.stderr


def test_an_app_still_refuses_a_default_admin_password(tmp_path):
    result = _import(tmp_path, env={"SUPERVISOR_TOKEN": "sup"}, admin_password="admin")
    assert result.returncode != 0 and "admin_password" in result.stderr


def test_the_app_offers_its_admin_panel_through_ingress():
    import ingress
    with open(os.path.join(APP_DIR, "config.yaml"), encoding="utf-8") as f:
        manifest = f.read()
    assert "\ningress: true\n" in manifest
    assert f"\ningress_port: {ingress.PORT}\n" in manifest
    assert "\npanel_admin: true\n" in manifest
    assert "\n  admin_password: password?\n" in manifest
    assert "8099" not in manifest.split("ports:")[1].split("ports_description:")[0]  # never on the host


def test_https_is_off_by_default(tmp_path):
    result = _import(tmp_path, "import config; print(config.ssl_enabled, config.session_cookie_secure)")
    assert result.stdout.split() == ["False", "False"], result.stderr


def test_https_looks_in_the_shared_ssl_folder_and_secures_the_cookie(tmp_path):
    _issue(tmp_path)
    code = "import config; print(config.ssl_certfile, config.ssl_keyfile, config.session_cookie_secure)"
    result = _import(tmp_path, code, env={"DOOROPENER_SSL_DIR": str(tmp_path)}, ssl=True)
    assert result.stdout.split() == [str(tmp_path / "fullchain.pem"), str(tmp_path / "privkey.pem"), "True"], \
        result.stderr


def test_https_takes_full_paths_as_given(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    _issue(live, "cert.pem", "key.pem")
    result = _import(tmp_path, "import config; print(config.ssl_certfile, config.ssl_keyfile)",
                     env={"DOOROPENER_SSL_DIR": "/nonexistent"},
                     ssl=True, certfile=str(live / "cert.pem"), keyfile=str(live / "key.pem"))
    assert result.stdout.split() == [str(live / "cert.pem"), str(live / "key.pem")], result.stderr


@pytest.mark.parametrize("certificate", ["missing", "with someone else's key"])
def test_https_refuses_to_start_without_a_usable_certificate(tmp_path, certificate):
    if certificate != "missing":
        _issue(tmp_path, key_from=trustme.CA().issue_cert("other.test"))
    result = _import(tmp_path, env={"DOOROPENER_SSL_DIR": str(tmp_path)}, ssl=True)
    assert result.returncode != 0
    assert "ssl is on" in result.stderr


def test_the_app_health_check_and_web_ui_follow_the_ssl_option():
    with open(os.path.join(APP_DIR, "config.yaml"), encoding="utf-8") as f:
        manifest = f.read()
    # The Supervisor resolves [PROTO:ssl] from the option named ssl.
    assert 'webui: "[PROTO:ssl]://' in manifest
    assert 'watchdog: "[PROTO:ssl]://' in manifest
    assert "\n  ssl: false\n" in manifest and "\n  ssl: bool\n" in manifest
    assert "\n  - ssl:ro\n" in manifest


@pytest.mark.parametrize("opts, env, expected", [
    ({}, {"SUPERVISOR_TOKEN": "sup"}, ("http://supervisor/core", "sup")),
    ({"ha_url": "http://supervisor/core"}, {"SUPERVISOR_TOKEN": "sup"}, ("http://supervisor/core", "sup")),
    # What run.sh used to write: the extra /api made every call 404.
    ({"ha_url": "http://supervisor/core/api"}, {"SUPERVISOR_TOKEN": "sup"}, ("http://supervisor/core", "sup")),
    ({"ha_url": "https://ha.lan:8123/", "ha_token": " llat "}, {"SUPERVISOR_TOKEN": "sup"},
     ("https://ha.lan:8123", "llat")),
    ({"ha_url": "https://ha.lan:8123/api/", "ha_token": "llat"}, {}, ("https://ha.lan:8123", "llat")),
])
def test_home_assistant_endpoint(opts, env, expected):
    assert config._ha_endpoint(opts, env) == expected


def test_health(client):
    import app as app_module
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok", "version": app_module.APP_VERSION}


def test_oversized_bodies_are_refused(client):
    r = client.post("/open-door", data=b'{"pin": "' + b"1" * 100_000 + b'"}', content_type="application/json")
    assert r.status_code == 413
    assert r.get_json() == {"status": "error", "message": "Request body too large"}


def test_the_service_worker_only_caches_the_shell_and_static_files():
    with open(os.path.join(APP_DIR, "static", "service-worker.js"), encoding="utf-8") as f:
        source = f.read()
    # One respondWith for "/" and one for /static/; anything else would be a catch-all
    # caching /admin/* responses.
    assert source.count("event.respondWith(") == 2
    assert "url.pathname === '/'" in source
    assert "url.pathname.startsWith('/static/')" in source


def test_pages_make_no_third_party_requests(client):
    for page in ("/", "/admin"):
        assert b"api.github.com" not in client.get(page).data
