"""Route-level behaviour tests: service worker, manifest, CSP, counters."""


def test_service_worker_endpoint(client):
    r = client.get("/service-worker.js")
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert "javascript" in (r.mimetype or "")


def test_service_worker_with_gzip_does_not_500(client):
    """Regression: gzip must skip passthrough (send_file) responses.

    A gzip-capable client (every browser) previously 500'd the SW because
    after_request called get_data() on a direct-passthrough response.
    """
    r = client.get("/service-worker.js", headers={"Accept-Encoding": "gzip"})
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert r.headers.get("Content-Encoding") != "gzip"


def test_manifest_endpoint(client):
    r = client.get("/manifest.webmanifest")
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert "application/manifest+json" in (r.mimetype or "")


def test_csp_directives_on_index(client):
    r = client.get("/")
    assert r.status_code == 200
    csp = r.headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "connect-src 'self';" in csp  # and nothing else: no third-party calls


def test_csp_uses_nonce_for_scripts(client):
    """CSP script-src should use nonce, style-src allows unsafe-inline for attribute styles."""
    r = client.get("/")
    csp = r.headers.get("Content-Security-Policy", "")
    assert "'nonce-" in csp
    # script-src uses nonce, not unsafe-inline
    script_src = [p for p in csp.split(";") if "script-src" in p][0]
    assert "'nonce-" in script_src
    assert "'unsafe-inline'" not in script_src
    # style-src allows unsafe-inline (needed for inline style="" attributes in Safari)
    style_src = [p for p in csp.split(";") if "style-src" in p][0]
    assert "'unsafe-inline'" in style_src


def test_success_forgets_earlier_failures(client, monkeypatch):
    """Wrong PINs, then the right one: the next wrong PINs count from zero again."""
    import app as app_module

    monkeypatch.setattr("app.client_identity", lambda: ("2.2.2.2", "sessReset"))
    app_module.users_store.create_user("ok", "9999")
    almost = app_module.rate_limiter.pin_session.limit - 1

    for _ in range(almost):
        assert client.post("/open-door", json={"pin": "0000"}).status_code == 401
    assert client.post("/open-door", json={"pin": "9999"}).status_code == 200
    for _ in range(almost):
        assert client.post("/open-door", json={"pin": "0000"}).status_code == 401
    assert app_module.rate_limiter.pin_blocked_until("2.2.2.2", "sessReset") is None


def test_correct_pin_during_a_block_is_refused_and_keeps_the_block(client, monkeypatch):
    import app as app_module

    monkeypatch.setattr("app.client_identity", lambda: ("3.3.3.3", "sessBlock"))
    app_module.users_store.create_user("ok2", "1357")
    throttle = app_module.rate_limiter.pin_session
    for _ in range(throttle.limit):
        throttle.fail("sessBlock")

    r = client.post("/open-door", json={"pin": "1357"})

    assert r.status_code == 429
    assert throttle.blocked_until("sessBlock") is not None
