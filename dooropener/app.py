#!/usr/bin/env python3
"""DoorOpener: a PIN keypad that opens a door through Home Assistant.

This module holds the routes and the request plumbing. The rest lives next door:
settings in ``config``, brute-force protection in ``security``, PIN rules in
``pins``, when a PIN works in ``access``, users in ``users_store``, the audit
trail in ``audit``, the Home Assistant calls in ``ha_client`` and the admin panel
inside Home Assistant in ``ingress``.

The admin panel answers in two places. Through ingress, Home Assistant has
already logged the user in, so a Home Assistant administrator gets straight in
and no cookie is involved. On DoorOpener's own port, it takes the admin password,
and is not offered at all when none is set (allowed only in an app).
"""

import atexit
import gzip
import hashlib
import hmac
import logging
import math
import os
import secrets
import threading
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, jsonify, render_template, request, send_from_directory, session, url_for
from flask.sessions import SecureCookieSessionInterface
from werkzeug.exceptions import InternalServerError, RequestEntityTooLarge

import config
import ingress
from audit import AuditLog
from config import get_current_time
from ha_client import HAClient, HAError
from pins import DEFAULT_PIN_LENGTH, PIN_MAX_LENGTH, PIN_MIN_LENGTH, generate_pin, is_valid_pin, secure_equals
from security import LocalProxyFix, RateLimiter, add_security_headers, client_identity
from users_store import KEEP, InvalidUser, UserConflict, UserNotFound, UsersStore, UsersStoreError

# Stamped into the image from config.yaml, which the Supervisor passes to the
# build as BUILD_VERSION.
APP_VERSION = os.environ.get("APP_VERSION", "0.0.0")

logger = logging.getLogger("dooropener")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _sh = logging.StreamHandler()
    _sh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(_sh)

# ---------------------------------------------------------------------------
# Application and shared objects
# ---------------------------------------------------------------------------
app = Flask(__name__)
if config.trusted_proxies:
    app.wsgi_app = LocalProxyFix(app.wsgi_app, config.trusted_proxies)
app.secret_key = config.secret_key or secrets.token_hex(32)


class _SessionInterface(SecureCookieSessionInterface):
    """The session cookie is HTTPS-only whenever the request itself came over HTTPS.

    That covers DoorOpener's own HTTPS and a trusted reverse proxy's (whose scheme
    LocalProxyFix applies), and still works over plain HTTP, where a Secure
    cookie would never come back and the keypad's CSRF check would always fail.
    """

    def get_cookie_secure(self, app):
        return request.is_secure


app.session_interface = _SessionInterface()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_PATH="/",
    PERMANENT_SESSION_LIFETIME=config.ADMIN_REMEMBER_MAX_AGE,
    MAX_CONTENT_LENGTH=64 * 1024,  # every endpoint takes a tiny JSON body
    CSRF_ENABLED=True,
)

_HERE = os.path.dirname(__file__)
USERS_STORE_PATH = os.environ.get("USERS_STORE_PATH", os.path.join(_HERE, "users.json"))
LOG_DIR = os.environ.get("DOOROPENER_LOG_DIR") or os.path.join(_HERE, "logs")

rate_limiter = RateLimiter()
ha_client = HAClient()
users_store = UsersStore(USERS_STORE_PATH)
audit_log = AuditLog(os.path.join(LOG_DIR, "log.txt"))

# Persist any coalesced usage counters on shutdown (record_use batches writes).
atexit.register(users_store.flush_touches)

if config.random_secret_warning:
    logger.warning("No secret_key configured: using a random one, so logins end at every restart.")
if config.test_mode:
    logger.warning("TEST MODE ENABLED: the door will NOT open. Disable test_mode before production use.")


def start_background_services() -> None:
    """Run in each gunicorn worker once the app is loaded (see gunicorn.conf.py)."""
    _in_background(ha_client.check_connection)
    if config.ha_app:
        ingress.serve(app)


# ---------------------------------------------------------------------------
# Request plumbing
# ---------------------------------------------------------------------------
def _visitor() -> ingress.Visitor | None:
    """The Home Assistant user, when the request came through Home Assistant (ingress)."""
    return ingress.visitor(request.environ)


@app.context_processor
def _template_helpers():
    # Asset URLs carry the version, so browsers may keep them until the next release.
    return {"asset": lambda name: url_for("static", filename=name, v=APP_VERSION), "app_version": APP_VERSION}


@app.before_request
def _prepare_request():
    """A CSRF token for every session (ingress requests have no session; see _csrf_token)."""
    if _visitor() is None:
        session.setdefault("_csrf_token", secrets.token_hex(32))


@app.after_request
def _finish_response(response):
    # Home Assistant shows the ingress pages in a frame of its own (same origin).
    response = add_security_headers(response, framable=_visitor() is not None)
    # Responses default to no-store. Static files may be cached: for good when the
    # URL names the version, for a day otherwise. The service worker itself is
    # always revalidated.
    cache = None
    if request.endpoint == "service_worker":
        cache = "no-cache"
    elif request.endpoint == "static":
        cache = "public, max-age=31536000, immutable" if request.args.get("v") else "public, max-age=86400"
    elif request.endpoint == "manifest_file":
        cache = "public, max-age=3600"
    if cache:
        response.headers["Cache-Control"] = cache
        response.headers.pop("Pragma", None)
    return _gzip(response)


_COMPRESSIBLE = ("text/", "application/json", "javascript", "manifest+json", "image/svg+xml")


def _gzip(response):
    """Compress text responses over 512 bytes, static files included, for clients that accept gzip."""
    if (
        response.status_code != 200
        or "Content-Encoding" in response.headers
        or not any(t in (response.content_type or "") for t in _COMPRESSIBLE)
        or "gzip" not in request.headers.get("Accept-Encoding", "")
    ):
        return response
    # A static file comes as a lazy file wrapper; the text ones are small enough to read.
    response.direct_passthrough = False
    data = response.get_data()
    compressed = gzip.compress(data, compresslevel=6)
    if len(data) > 512 and len(compressed) < len(data):
        response.set_data(compressed)  # also updates Content-Length
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Vary"] = "Accept-Encoding"
        etag = response.headers.get("ETag")
        if etag and etag.endswith('"'):  # a different body needs a different tag
            response.headers["ETag"] = etag[:-1] + '-gzip"'
    return response


def _error(message: str, code: int, **extra):
    """The one shape every JSON error takes."""
    return jsonify({"status": "error", "message": message, **extra}), code


@app.errorhandler(RequestEntityTooLarge)
def _too_large(_e):
    return _error("Request body too large", 413)


@app.errorhandler(InvalidUser)
def _invalid_user(e):
    return _error(str(e), 400)


@app.errorhandler(UserNotFound)
def _user_not_found(e):
    return _error(str(e), 404)


@app.errorhandler(UserConflict)
def _user_conflict(e):
    return _error(str(e), 409)


@app.errorhandler(UsersStoreError)
def _users_store_unavailable(e):
    # A corrupt users.json is left untouched rather than read as "no users".
    logger.error("User store unavailable: %s", e)
    return _error("User store unavailable (corrupt or unreadable); nothing was changed", 503)


@app.errorhandler(InternalServerError)
def _internal_error(_e):
    return _error("Internal server error", 500)


def _csrf_token() -> str:
    """The token pages send back in X-CSRF-Token.

    Through ingress there is no session cookie, so the token is bound to the
    ingress session's path instead, which only that browser knows.
    """
    if _visitor() is not None:
        return hmac.new(app.secret_key.encode(), request.script_root.encode(), hashlib.sha256).hexdigest()
    return session.get("_csrf_token", "")


def csrf_protected(view):
    """Reject the request unless X-CSRF-Token matches the expected token."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if app.config["CSRF_ENABLED"]:
            token = request.headers.get("X-CSRF-Token", "")
            expected = _csrf_token()
            if not token or not expected or not secure_equals(token, expected):
                return _error("Invalid or missing CSRF token", 403)
        return view(*args, **kwargs)
    return wrapper


def require_admin(view):
    """Reject the request unless it may use the admin panel (see _admin_refusal)."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        refusal = _admin_refusal()
        if refusal is not None:
            return refusal
        return view(*args, **kwargs)
    return wrapper


def _admin_refusal():
    """None for a request that may use the admin panel, else the error to answer it with."""
    visitor = _visitor()
    if visitor is not None:
        return None if visitor.admin else _error("For Home Assistant administrators only", 403)
    if not config.public_admin:
        return _error("The admin panel is in Home Assistant", 404)
    if not _admin_session_valid():
        return _error("Authentication required", 401)
    return None


def _admin_session_valid() -> bool:
    """True for a logged-in admin whose session is younger than its maximum age.

    The session is a signed cookie, so a stolen copy outlives any logout; the age
    limit, counted from the login time signed into the cookie, is what bounds it.
    """
    if not session.get("admin_authenticated"):
        return False
    try:
        age = get_current_time() - datetime.fromisoformat(session["admin_login_time"])
    except (KeyError, TypeError, ValueError):  # missing, malformed or naive timestamp
        age = None
    max_age = config.ADMIN_REMEMBER_MAX_AGE if session.permanent else config.ADMIN_SESSION_MAX_AGE
    if age is None or not timedelta(minutes=-1) <= age <= max_age:
        _end_admin_session()
        return False
    return True


def _end_admin_session() -> None:
    session.pop("admin_authenticated", None)
    session.pop("admin_login_time", None)
    session.permanent = False


def _json_body() -> dict:
    """The request's JSON object, or {} for anything else."""
    body = request.get_json(force=True, silent=True)
    return body if isinstance(body, dict) else {}


def _minutes_until(moment: datetime) -> int:
    return max(1, math.ceil((moment - get_current_time()).total_seconds() / 60))


def _blocked(until: datetime):
    return _error("Too many failed attempts. Please try again later.", 429, blocked_until=until.timestamp())


def _count_failure(ip: str, session_id: str) -> None:
    """Count a wrong PIN or admin password toward the hourly alert threshold.

    Crossing it writes a GLOBAL_THRESHOLD audit entry and, outside test mode,
    raises a Home Assistant notification. Nobody is locked out.
    """
    if not rate_limiter.alarm.record():
        return
    attempts = rate_limiter.alarm.threshold
    audit_log.record(ip, session_id, "UNKNOWN", "GLOBAL_THRESHOLD", f"{attempts} failed attempts within an hour")
    logger.warning("%d failed attempts within an hour, the latest from %s", attempts, ip)
    if not config.test_mode:
        message = (
            f"{attempts} wrong PINs or admin passwords within an hour, the latest from {ip}. "
            "The audit log in the DoorOpener admin panel has the details."
        )
        _in_background(ha_client.notify, "DoorOpener: possible brute force", message)


def _in_background(fn, *args) -> None:
    """Run *fn* without holding up the response."""
    threading.Thread(target=fn, args=args, daemon=True).start()


def _audit_admin(status: str, details: str) -> None:
    visitor = _visitor()
    if visitor is not None:
        audit_log.record("Home Assistant", "ingress", f"ADMIN ({visitor.name})", status, details)
        return
    ip, session_id = client_identity()
    audit_log.record(ip, session_id, "ADMIN", status, details)


# ---------------------------------------------------------------------------
# Keypad
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    if _visitor() is not None:  # Home Assistant opens straight onto the admin panel
        return admin()
    return render_template(
        "index.html",
        csrf_token=_csrf_token(),
        battery_enabled=bool(config.battery_entity),
        public_admin=config.public_admin,
    )


@app.route("/service-worker.js")
def service_worker():
    return send_from_directory(app.static_folder, "service-worker.js", mimetype="application/javascript")


@app.route("/manifest.webmanifest")
def manifest_file():
    return send_from_directory(app.static_folder, "manifest.webmanifest", mimetype="application/manifest+json")


@app.route("/health")
def health():
    """Liveness probe for the Supervisor watchdog."""
    return jsonify({"status": "ok", "version": APP_VERSION})


@app.route("/battery")
def battery():
    # get_battery_level() caches for 60s and returns None without a battery entity.
    return jsonify({"level": ha_client.get_battery_level()})


@app.route("/open-door", methods=["POST"])
@csrf_protected
def open_door():
    ip, session_id = client_identity()

    blocked_until = rate_limiter.pin_blocked_until(ip, session_id)
    if blocked_until:
        audit_log.record(ip, session_id, "UNKNOWN", "BLOCKED", f"Blocked for {_minutes_until(blocked_until)} more min")
        return _blocked(blocked_until)

    pin = _json_body().get("pin")
    if pin is None:
        return _error("PIN required", 400)
    if not is_valid_pin(pin):
        return _wrong_pin(ip, session_id, "INVALID_FORMAT", "Invalid PIN format", 400)

    user, refusal = users_store.authorize(pin, get_current_time())
    if refusal:
        # A disabled PIN, or one outside its access rules, is answered and counted
        # like any wrong PIN: anything else would let a stranger test, unthrottled,
        # which PINs exist. Only the audit log says whose it was and why.
        return _wrong_pin(ip, session_id, refusal, "Invalid PIN", 401, user=user or "UNKNOWN")

    rate_limiter.pin_succeeded(ip, session_id)
    return _open_door_for(user, ip, session_id)


def _wrong_pin(ip: str, session_id: str, status: str, message: str, code: int, user: str = "UNKNOWN"):
    blocked_until = rate_limiter.pin_failed(ip, session_id)
    _count_failure(ip, session_id)
    if not blocked_until:
        audit_log.record(ip, session_id, user, status, message)
        return _error(message, code)
    minutes = _minutes_until(blocked_until)
    audit_log.record(ip, session_id, user, status, f"{message}; blocked for {minutes} min")
    return _error(
        f"{message}. Too many failed attempts: try again in {minutes} min.",
        code,
        blocked_until=blocked_until.timestamp(),
    )


def _open_door_for(user: str, ip: str, session_id: str):
    suffix = " (TEST MODE)" if config.test_mode else ""
    if not config.test_mode:
        try:
            ha_client.open_door()
        except HAError as e:
            # A valid PIN that did not open the door belongs in the audit trail.
            logger.error("Opening the door failed: %s", e)
            audit_log.record(ip, session_id, user, "FAILURE", str(e))
            return _error("Failed to contact Home Assistant", 502)
    used_up = _record_use(user)
    audit_log.record(ip, session_id, user, "SUCCESS",
                     f"Door opened{suffix}" + ("; one-time PIN used up" if used_up else ""))
    if not config.test_mode:
        _in_background(ha_client.door_opened, user)
    return jsonify({
        "status": "success",
        "message": f"Door open command sent{suffix}.\nWelcome home, {user.capitalize()}!",
    })


def _record_use(username: str) -> bool:
    """Count a use; True if it used up a one-time PIN. Never fails a door that already opened."""
    try:
        return users_store.record_use(username)
    except Exception as e:
        logger.warning("Could not record a use for %s: %s", username, e)
        return False


# ---------------------------------------------------------------------------
# Admin: session
# ---------------------------------------------------------------------------
@app.route("/admin")
def admin():
    visitor = _visitor()
    if visitor is not None and not visitor.admin:
        return render_template("admin.html", notice="DoorOpener's admin panel is for Home Assistant "
                                                    "administrators."), 403
    if visitor is None and not config.public_admin:
        return render_template("admin.html", notice="The admin panel is in Home Assistant: Settings → "
                                                    "Apps → DoorOpener → Open Web UI."), 404
    return render_template(
        "admin.html",
        csrf_token=_csrf_token(),
        ha_user=visitor.name if visitor else None,
        is_authenticated=visitor is not None or _admin_session_valid(),
        test_mode=config.test_mode,
    )


def _password_login_only(view):
    """The password login exists only on DoorOpener's own port, and only with a password set."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if _visitor() is not None or not config.public_admin:
            return _error("Not available here", 404)
        return view(*args, **kwargs)
    return wrapper


@app.route("/admin/auth", methods=["POST"])
@_password_login_only
@csrf_protected
def admin_auth():
    ip, session_id = client_identity()

    blocked_until = rate_limiter.admin_ip.blocked_until(ip)
    if blocked_until:
        audit_log.record(ip, session_id, "ADMIN", "ADMIN_BLOCKED",
                         f"Blocked for {_minutes_until(blocked_until)} more min")
        return _blocked(blocked_until)

    body = _json_body()
    if not secure_equals(str(body.get("password") or "").strip(), config.admin_password):
        blocked_until = rate_limiter.admin_ip.fail(ip)
        _count_failure(ip, session_id)
        details = "Invalid admin password"
        if blocked_until:
            details += f"; blocked for {_minutes_until(blocked_until)} min"
        audit_log.record(ip, session_id, "ADMIN", "ADMIN_FAILURE", details)
        return _error("Invalid admin password", 403)

    rate_limiter.admin_ip.reset(ip)
    session["admin_authenticated"] = True
    session["admin_login_time"] = get_current_time().isoformat()
    session.permanent = bool(body.get("remember_me"))
    audit_log.record(ip, session_id, "ADMIN", "ADMIN_SUCCESS", "Admin login")
    return jsonify({"status": "success"})


@app.route("/admin/check-auth")
@_password_login_only
def admin_check_auth():
    if _admin_session_valid():
        return jsonify({"authenticated": True, "login_time": session["admin_login_time"]})
    return jsonify({"authenticated": False})


@app.route("/admin/logout", methods=["POST"])
@_password_login_only
@csrf_protected
def admin_logout():
    _end_admin_session()
    return jsonify({"status": "success", "message": "Logged out successfully"})


# ---------------------------------------------------------------------------
# Admin: audit log
# ---------------------------------------------------------------------------
@app.route("/admin/logs")
@require_admin
def admin_logs():
    return jsonify({"logs": audit_log.tail()})


@app.route("/admin/logs/clear", methods=["POST"])
@require_admin
@csrf_protected
def admin_logs_clear():
    mode = str(_json_body().get("mode") or "all").lower()
    if mode not in ("all", "test_only"):
        return _error("Invalid mode", 400)
    removed, kept = audit_log.clear(test_only=mode == "test_only")
    _audit_admin("ADMIN_LOGS_CLEAR", f"mode={mode}, removed={removed}, kept={kept}")
    return jsonify({"status": "ok", "mode": mode, "removed": removed, "kept": kept})


# ---------------------------------------------------------------------------
# Admin: users
# ---------------------------------------------------------------------------
@app.route("/admin/users")
@require_admin
def admin_users_list():
    return jsonify({"users": [{**user, "can_edit": True} for user in users_store.list_users()["users"]]})


@app.route("/admin/users", methods=["POST"])
@require_admin
@csrf_protected
def admin_users_create():
    body = _json_body()
    username, pin = body.get("username"), body.get("pin")
    if not username or not pin:
        return _error("username and pin are required", 400)
    users_store.create_user(username, pin, bool(body.get("active", True)), body.get("access"))
    _audit_admin("ADMIN_USER_CREATE", f"username={username}")
    return jsonify({"status": "created"}), 201


@app.route("/admin/users/<username>", methods=["PUT"])
@require_admin
@csrf_protected
def admin_users_update(username: str):
    body = _json_body()
    users_store.update_user(username, pin=body.get("pin"), active=body.get("active"),
                            access_rules=body.get("access", KEEP))
    _audit_admin("ADMIN_USER_UPDATE", f"username={username}")
    return jsonify({"status": "updated"})


@app.route("/admin/users/<username>", methods=["DELETE"])
@require_admin
@csrf_protected
def admin_users_delete(username: str):
    users_store.delete_user(username)
    _audit_admin("ADMIN_USER_DELETE", f"username={username}")
    return jsonify({"status": "deleted"})


@app.route("/admin/generate-pin")
@require_admin
def admin_generate_pin():
    """A random PIN that no user has (disabled ones included) and nobody would guess first.

    Nothing is reserved: creating the user still checks for duplicates.
    """
    try:
        length = int(request.args.get("length", DEFAULT_PIN_LENGTH))
    except ValueError:
        length = 0
    if not PIN_MIN_LENGTH <= length <= PIN_MAX_LENGTH:
        return _error(f"length must be {PIN_MIN_LENGTH}-{PIN_MAX_LENGTH}", 400)
    pin = generate_pin(length, users_store.pin_taken)
    if pin is None:
        return _error(f"No unused {length}-digit PIN is left", 409)
    return jsonify({"pin": pin})


# ---------------------------------------------------------------------------
# Entry point (development only; the add-on runs gunicorn)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    _debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    if _debug:
        logger.warning(
            "FLASK_DEBUG is enabled: the Werkzeug debugger allows remote code execution "
            "from the browser. NEVER enable it in production."
        )
    app.run(host="0.0.0.0", port=config.server_port, debug=_debug)  # nosec B104
