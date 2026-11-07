"""HTTPS served by DoorOpener itself, and certificates that renew in place.

Renewals are written the way certbot and the Let's Encrypt app leave them: each
file replaced whole, the certificate before the key.
"""
import json
import logging
import os
import shutil
import socket
import ssl
import subprocess  # nosec B404 - runs this repo's own server with fixed arguments
import sys
import threading
import time
import urllib.request

import pytest
import trustme

import tls

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dooropener"))
GUNICORN = shutil.which("gunicorn", path=os.path.dirname(sys.executable)) or shutil.which("gunicorn")


@pytest.fixture(scope="module")
def ca():
    return trustme.CA()


def _install(directory, leaf, *, cert=True, key=True):
    if cert:
        _replace(directory / "fullchain.pem", b"".join(pem.bytes() for pem in leaf.cert_chain_pems))
    if key:
        _replace(directory / "privkey.pem", leaf.private_key_pem.bytes())


def _replace(path, data):
    staged = path.with_name(path.name + ".new")
    staged.write_bytes(data)
    os.replace(staged, path)


def _der(leaf):
    return ssl.PEM_cert_to_DER_cert(leaf.cert_chain_pems[0].bytes().decode())


def _served(context, server_name="door.test"):
    """The certificate a client is shown on a new connection (no SNI when server_name is None)."""
    server_end, client_end = socket.socketpair()
    with server_end, client_end:
        server = threading.Thread(target=_handshake, args=(context, server_end))
        server.start()
        client = ssl.create_default_context()
        client.check_hostname = False
        client.verify_mode = ssl.CERT_NONE
        with client.wrap_socket(client_end, server_hostname=server_name) as conn:
            served = conn.getpeercert(binary_form=True)
        server.join(timeout=5)
    return served


def _handshake(context, sock):
    try:
        context.wrap_socket(sock, server_side=True).close()
    except OSError:
        pass  # the client side reports what went wrong


@pytest.fixture
def certs(tmp_path, ca):
    first = ca.issue_cert("door.test")
    _install(tmp_path, first)
    certificate = tls.Certificate(str(tmp_path / "fullchain.pem"), str(tmp_path / "privkey.pem"))
    return tmp_path, first, certificate


def test_serves_its_certificate(certs):
    _, first, certificate = certs
    assert _served(certificate.context) == _der(first)


@pytest.mark.parametrize("server_name", ["door.test", None], ids=["by-name", "by-ip"])
def test_a_renewal_is_served_on_the_next_connection(certs, ca, server_name):
    directory, _, certificate = certs
    renewed = ca.issue_cert("door.test")
    _install(directory, renewed)
    assert _served(certificate.context, server_name) == _der(renewed)


def test_a_renewal_caught_halfway_keeps_the_old_certificate(certs, ca, caplog):
    directory, first, certificate = certs
    renewed = ca.issue_cert("door.test")
    _install(directory, renewed, key=False)  # new certificate, old key: not a pair
    with caplog.at_level(logging.WARNING, logger="dooropener"):
        assert _served(certificate.context) == _der(first)
        assert _served(certificate.context) == _der(first)
    assert caplog.text.count("Keeping the current TLS certificate") == 1  # once, not per connection

    _install(directory, renewed, cert=False)  # the key follows
    assert _served(certificate.context) == _der(renewed)


def test_missing_files_keep_the_last_good_certificate(certs):
    directory, first, certificate = certs
    (directory / "fullchain.pem").unlink()
    assert _served(certificate.context) == _der(first)


def test_files_that_do_not_load_are_refused_up_front(tmp_path):
    with pytest.raises(OSError):
        tls.Certificate(str(tmp_path / "fullchain.pem"), str(tmp_path / "privkey.pem"))


# ---------------------------------------------------------------------------
# The real server, started the way both the app and the container start it
# ---------------------------------------------------------------------------
def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    """Start ``gunicorn app:app`` in the app directory with these options; returns its port."""
    started = []

    def start(**options):
        port = _free_port()
        opts = {"admin_password": "a-real-password", "entity_id": "switch.door", "test_mode": True,
                "port": port, **options}
        (tmp_path / "options.json").write_text(json.dumps(opts))
        env = {k: v for k, v in os.environ.items()
               if k not in ("FLASK_SECRET_KEY", "SUPERVISOR_TOKEN", "DOOROPENER_PORT")}
        env.update(DOOROPENER_OPTIONS_PATH=str(tmp_path / "options.json"), DOOROPENER_SSL_DIR=str(tmp_path),
                   DOOROPENER_LOG_DIR=str(tmp_path / "logs"), USERS_STORE_PATH=str(tmp_path / "users.json"))
        proc = subprocess.Popen(  # nosec B603
            [GUNICORN, "app:app"], cwd=APP_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        started.append(proc)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            assert proc.poll() is None, proc.stdout.read().decode()
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                return port
            except OSError:
                time.sleep(0.1)
        pytest.fail("gunicorn did not start listening")

    yield start
    for proc in started:
        proc.terminate()
        proc.wait(timeout=10)
        proc.stdout.close()


def _health(url, context=None):
    with urllib.request.urlopen(url, context=context, timeout=5) as r:  # nosec B310 - local test server
        return json.load(r)["status"]


def test_without_ssl_the_server_speaks_plain_http(server):
    port = server()
    assert _health(f"http://127.0.0.1:{port}/health") == "ok"


def test_with_ssl_the_server_speaks_https_and_follows_renewals(server, tmp_path, ca):
    _install(tmp_path, ca.issue_cert("localhost"))
    port = server(ssl=True)

    trusting = ssl.create_default_context()
    ca.configure_trust(trusting)
    assert _health(f"https://localhost:{port}/health", trusting) == "ok"

    renewed = ca.issue_cert("localhost")
    _install(tmp_path, renewed)
    assert ssl.PEM_cert_to_DER_cert(ssl.get_server_certificate(("127.0.0.1", port))) == _der(renewed)
