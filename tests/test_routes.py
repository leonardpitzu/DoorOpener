"""Route-level behaviour tests: service worker, manifest, CSP, counters."""


def test_service_worker_endpoint(client):
    r = client.get("/service-worker.js")
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert "javascript" in (r.mimetype or "")


def test_static_text_files_are_gzipped(client):
    """Files sent from disk (send_file) are compressed too, and come out intact.

    Regression guard: reading a direct-passthrough body once 500'd every request
    from a gzip-capable browser.
    """
    import gzip
    for path in ("/service-worker.js", "/static/keypad.js", "/static/admin.css"):
        plain = client.get(path)
        zipped = client.get(path, headers={"Accept-Encoding": "gzip"})
        assert zipped.status_code == 200, path
        assert zipped.headers["Content-Encoding"] == "gzip"
        assert gzip.decompress(zipped.data) == plain.data


def test_images_are_sent_as_they_are(client):
    r = client.get("/static/background.webp", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and "Content-Encoding" not in r.headers


def test_versioned_assets_are_cached_for_good(client):
    assert "immutable" in client.get("/static/keypad.js?v=2.8.0").headers["Cache-Control"]
    assert client.get("/static/keypad.js").headers["Cache-Control"] == "public, max-age=86400"
    assert client.get("/service-worker.js").headers["Cache-Control"] == "no-cache"
    assert "no-store" in client.get("/").headers["Cache-Control"]


def test_pages_link_their_assets_by_version(client):
    import app as app_module
    page = client.get("/").get_data(as_text=True)
    assert f"/static/keypad.js?v={app_module.APP_VERSION}" in page
    assert f"/static/keypad.css?v={app_module.APP_VERSION}" in page


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


def test_csp_allows_only_scripts_from_dooropener_itself(client):
    """Scripts are static files now: no inline script at all, so no nonce either."""
    r = client.get("/")
    csp = r.headers.get("Content-Security-Policy", "")
    script_src = [p for p in csp.split(";") if "script-src" in p][0]
    assert script_src.strip() == "script-src 'self'"
    assert "<script>" not in r.get_data(as_text=True)
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


def test_the_session_cookie_is_https_only_exactly_when_the_request_is_https(client):
    plain = client.get("/").headers["Set-Cookie"]
    secure = client.get("/", base_url="https://door.example").headers["Set-Cookie"]
    assert "Secure" not in plain  # over plain HTTP it would never come back
    assert "Secure" in secure and "HttpOnly" in secure
