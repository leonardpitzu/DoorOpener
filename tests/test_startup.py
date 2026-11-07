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
import yaml

import config

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dooropener"))
GOOD = {"admin_password": "a-real-password", "doors": [{"id": "door", "name": "Door", "entity_id": "switch.door"}]}


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


def test_without_a_key_one_is_kept_in_the_key_file(tmp_path):
    key_file = tmp_path / "secret_key"
    code = "import config; print(config.secret_key, config.random_secret_warning)"
    first = _import(tmp_path, code, env={"DOOROPENER_SECRET_KEY_FILE": str(key_file)})
    key, warned = first.stdout.split()
    assert len(key) == 64 and warned == "False", first.stderr
    assert key_file.read_text().strip() == key
    assert key_file.stat().st_mode & 0o777 == 0o600  # inside the container's /data
    again = _import(tmp_path, code, env={"DOOROPENER_SECRET_KEY_FILE": str(key_file)})
    assert again.stdout.split()[0] == key  # the same key after a restart


def test_a_configured_key_wins_over_the_key_file(tmp_path):
    key_file = tmp_path / "secret_key"
    result = _import(tmp_path, "import config; print(config.secret_key)",
                     env={"DOOROPENER_SECRET_KEY_FILE": str(key_file)}, secret_key="a-configured-key-0123456789")
    assert result.stdout.strip() == "a-configured-key-0123456789"
    assert not key_file.exists()


def test_an_empty_secret_key_means_a_random_one(tmp_path):
    result = _import(tmp_path, "import config; print(config.secret_key, config.random_secret_warning)")
    assert result.stdout.split() == ["None", "True"]


@pytest.mark.parametrize("proxies, wrapped", [(0, False), (1, True)])
def test_x_forwarded_for_is_trusted_only_when_configured(tmp_path, proxies, wrapped):
    code = "import app, security; print(isinstance(app.app.wsgi_app, security.LocalProxyFix))"
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
    assert "8099" not in manifest.split("ports:")[1].split("ports_description:")[0]  # never on the host


def test_https_is_off_by_default(tmp_path):
    result = _import(tmp_path, "import config; print(config.ssl_enabled)")
    assert result.stdout.split() == ["False"], result.stderr


def test_https_looks_in_the_shared_ssl_folder(tmp_path):
    _issue(tmp_path)
    code = "import config; print(config.ssl_certfile, config.ssl_keyfile)"
    result = _import(tmp_path, code, env={"DOOROPENER_SSL_DIR": str(tmp_path)}, ssl=True)
    assert result.stdout.split() == [str(tmp_path / "fullchain.pem"), str(tmp_path / "privkey.pem")], result.stderr


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


def test_the_health_check_works_with_or_without_https():
    import ingress
    manifest = _yaml("config.yaml")
    # The Supervisor reads [PROTO:...] only from top-level options, and HTTPS sits
    # in the settings section, so the check uses the always-plain ingress listener.
    assert manifest["watchdog"] == f"http://[HOST]:[PORT:{ingress.PORT}]/health"
    assert manifest["options"]["settings"]["ssl"] is False and manifest["schema"]["settings"]["ssl"] == "bool"
    assert "ssl:ro" in manifest["map"]


def test_the_supervisor_can_ask_the_ingress_listener_for_health():
    from werkzeug.test import Client
    import app as app_module
    import ingress
    r = Client(ingress.IngressApp(app_module.app)).get("/health", environ_base={"REMOTE_ADDR": ingress.SUPERVISOR_IP})
    assert r.status_code == 200 and r.get_json()["status"] == "ok"


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


# ---------------------------------------------------------------------------
# The app's configuration page
# ---------------------------------------------------------------------------
def _yaml(name):
    with open(os.path.join(APP_DIR, name), encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_the_page_shows_the_doors_first_then_doorOpeners_own_settings():
    manifest = _yaml("config.yaml")
    schema = manifest["schema"]
    assert list(schema) == ["doors", "settings"]
    (door,) = schema["doors"]  # a list of doors, each a small form
    assert list(door) == ["name", "id", "entity_id", "battery_entity", "icon"]
    assert manifest["options"]["doors"] == []  # a new install starts with none
    assert list(schema["settings"])[:3] == ["ssl", "certfile", "keyfile"]
    assert manifest["options"]["settings"]["report_to_ha"] is True


def test_the_app_asks_only_for_what_an_app_needs():
    schema = _yaml("config.yaml")["schema"]
    asked = set(schema) | set(schema["settings"])
    # The Supervisor connection, the container port and a kept key cover these.
    assert not {"ha_url", "ha_token", "ha_cert_pem", "port", "secret_key", "session_cookie_secure"} & asked
    assert schema["settings"]["admin_password"] == "password?"


def test_every_option_has_a_name_and_a_description():
    manifest, words = _yaml("config.yaml"), _yaml("translations/en.yaml")
    texts = words["configuration"]
    assert set(texts) == set(manifest["schema"])
    assert set(texts["settings"]["fields"]) == set(manifest["schema"]["settings"])
    assert set(texts["doors"]["fields"]) == set(manifest["schema"]["doors"][0])
    for key, text in [*texts.items(), *texts["settings"]["fields"].items(), *texts["doors"]["fields"].items()]:
        assert text["name"] and len(text["description"]) > 20, key
    assert set(words["network"]) == set(manifest["ports"])
    assert "(file name in /ssl)" in texts["settings"]["fields"]["certfile"]["name"]


def test_settings_are_read_from_their_section(tmp_path):
    code = "import config; print(config.test_mode, config.MAX_ATTEMPTS, config.trusted_proxies, config.public_admin)"
    result = _import(tmp_path, code,
                     settings={"test_mode": True, "max_attempts": 7, "trusted_proxies": 1, "admin_password": "a-section-pw"})
    assert result.stdout.split() == ["True", "7", "1", "True"], result.stderr


@pytest.mark.parametrize("option, good, bad", [
    ("id", ["barrier", "door", "front-door", "gate_2"], ["", "Barrier", "front door", "-door", "a" * 25]),
    ("entity_id", ["button.intercom_open", "switch.front_door", "lock.door", "input_boolean.buzz",
                   "cover.barrier", "script.open_gate"],
     ["", "intercom_open", "sensor.door", "button.Front"]),
    ("battery_entity", ["", "sensor.door_battery"], ["switch.door", "sensor."]),
])
def test_door_fields_are_checked_as_the_supervisor_will(option, good, bad):
    import re
    rule = _yaml("config.yaml")["schema"]["doors"][0][option]
    pattern = re.fullmatch(r"match\((.*)\)\??", rule).group(1)  # the Supervisor's own parsing
    assert all(re.match(pattern, value) for value in good)
    assert not any(re.match(pattern, value) for value in bad)


def _doors(tmp_path, doors=None, **options):
    code = "import config; print([(d.id, d.name, d.entity_id, d.battery_entity, d.icon) for d in config.doors])"
    if doors is not None:
        options["doors"] = doors
    return _import(tmp_path, code, **options)


def test_doors_are_read_in_order(tmp_path):
    result = _doors(tmp_path, [{"id": "barrier", "name": "Barrier", "entity_id": "cover.barrier", "icon": "barrier"},
                               {"id": "door", "name": "Door", "entity_id": "button.intercom_open",
                                "battery_entity": "sensor.door_battery"}])
    assert result.stdout.strip() == str([("barrier", "Barrier", "cover.barrier", "", "barrier"),
                                         ("door", "Door", "button.intercom_open", "sensor.door_battery", "door")])


def test_a_standalone_options_file_from_before_3_0_is_one_door(tmp_path):
    opts = {k: v for k, v in GOOD.items() if k != "doors"}
    code = "import config; print([(d.id, d.name, d.entity_id, d.battery_entity) for d in config.doors])"
    (tmp_path / "options.json").write_text(json.dumps({**opts, "entity_id": "switch.old", "battery_entity": "sensor.b"}))
    run_env = {k: v for k, v in os.environ.items() if k not in ("FLASK_SECRET_KEY", "SUPERVISOR_TOKEN")}
    run_env.update(DOOROPENER_OPTIONS_PATH=str(tmp_path / "options.json"), USERS_STORE_PATH=str(tmp_path / "u.json"))
    result = subprocess.run([sys.executable, "-c", code], cwd=APP_DIR, env=run_env,  # nosec B603
                            capture_output=True, text=True, timeout=60)
    assert result.stdout.strip() == str([("door", "Door", "switch.old", "sensor.b")]), result.stderr


def test_no_doors_still_starts(tmp_path):
    result = _doors(tmp_path, [])
    assert result.returncode == 0 and result.stdout.strip() == "[]", result.stderr


@pytest.mark.parametrize("doors, problem", [
    ([{"id": "door", "name": "A", "entity_id": "switch.a"}, {"id": "door", "name": "B", "entity_id": "switch.b"}],
     "same id"),
    ([{"id": "admin", "name": "Admin", "entity_id": "switch.a"}], "/admin"),
    ([{"id": "Front Door", "name": "Front", "entity_id": "switch.a"}], "lowercase"),
    ([{"id": "door", "name": "Door", "entity_id": "sensor.door"}], "entity"),
    ([{"id": "door", "name": "Door", "entity_id": "switch.a", "battery_entity": "switch.b"}], "battery"),
    ([{"id": "door", "name": "Door", "entity_id": "switch.a", "icon": "castle"}], "icon"),
    ("door", "list"),
])
def test_doors_that_cannot_work_refuse_to_start(tmp_path, doors, problem):
    result = _doors(tmp_path, doors)
    assert result.returncode != 0 and problem in result.stderr
