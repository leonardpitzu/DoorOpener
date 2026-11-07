"""Configuration loader for DoorOpener.

Reads ``options.json`` (path overridable via ``DOOROPENER_OPTIONS_PATH`` env var)
and exposes all settings as module-level attributes.
"""

import json
import logging
import os
from collections.abc import Mapping
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger("dooropener")

# ---------------------------------------------------------------------------
# Timezone
# ---------------------------------------------------------------------------
TZ = os.environ.get("TZ", "UTC")
try:
    TIMEZONE = ZoneInfo(TZ)
except Exception:
    logger.warning("Unknown timezone '%s', falling back to UTC", TZ)
    TIMEZONE = ZoneInfo("UTC")
    TZ = "UTC"


def get_current_time() -> datetime:
    """Return the current time in the configured timezone."""
    return datetime.now(TIMEZONE)


# ---------------------------------------------------------------------------
# Load options.json
# ---------------------------------------------------------------------------
OPTIONS_PATH = os.environ.get(
    "DOOROPENER_OPTIONS_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "options.json"),
)

try:
    with open(OPTIONS_PATH, "r", encoding="utf-8") as _f:
        _opts = json.load(_f)
except FileNotFoundError as _e:
    raise RuntimeError(
        f"Options file not found: {OPTIONS_PATH}. "
        "Copy options.json.example to options.json and configure it."
    ) from _e

# ---------------------------------------------------------------------------
# Home Assistant
# ---------------------------------------------------------------------------
SUPERVISOR_URL = "http://supervisor/core"


def _ha_endpoint(opts: Mapping, env: Mapping[str, str]) -> tuple[str, str]:
    """The Home Assistant base URL and access token to use.

    Defaults to the Supervisor proxy, and to the Supervisor's own token when
    ``ha_token`` is left empty. A trailing ``/api`` is dropped, since every call
    appends ``/api/...`` itself.
    """
    url = (opts.get("ha_url") or SUPERVISOR_URL).strip().rstrip("/").removesuffix("/api")
    token = (opts.get("ha_token") or "").strip() or env.get("SUPERVISOR_TOKEN", "")
    return url, token


ha_url, ha_token = _ha_endpoint(_opts, os.environ)
entity_id: str = (_opts.get("entity_id") or "").strip()

_entity_parts = entity_id.split(".", 1)
device_name: str = _entity_parts[1] if len(_entity_parts) == 2 else entity_id

battery_entity: str = (_opts.get("battery_entity") or "").strip()

# ---------------------------------------------------------------------------
# Inline PEM certificate support
# ---------------------------------------------------------------------------
_ha_cert_pem: str = (_opts.get("ha_cert_pem") or "").strip()
ha_ca_bundle: str = ""

if _ha_cert_pem and "BEGIN CERTIFICATE" in _ha_cert_pem:
    import tempfile

    _cert_fd, _cert_path = tempfile.mkstemp(suffix=".pem", prefix="dooropener-ca-")
    with os.fdopen(_cert_fd, "w") as _cf:
        _cf.write(_ha_cert_pem)
        if not _ha_cert_pem.endswith("\n"):
            _cf.write("\n")
    ha_ca_bundle = _cert_path
    logger.info("Wrote inline HA certificate to %s", _cert_path)
elif os.getenv("REQUESTS_CA_BUNDLE", "").strip():
    ha_ca_bundle = os.getenv("REQUESTS_CA_BUNDLE", "").strip()
    if not os.path.exists(ha_ca_bundle):
        logger.warning(
            "REQUESTS_CA_BUNDLE not found: %s. Falling back to system trust store.",
            ha_ca_bundle,
        )
        ha_ca_bundle = ""

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
server_port: int = int(os.environ.get("DOOROPENER_PORT", _opts.get("port", 6532)))
test_mode: bool = bool(_opts.get("test_mode", False))

# Reverse proxies in front of the app whose X-Forwarded-For we believe. Every
# throttle keys on the client IP, so trusting a hop that is not there lets anyone
# who reaches the port directly pick their own IP. 0 = clients connect directly.
trusted_proxies: int = int(_opts.get("trusted_proxies", 0))

# ---------------------------------------------------------------------------
# Security thresholds
# ---------------------------------------------------------------------------
MAX_ATTEMPTS: int = int(_opts.get("max_attempts", 5))
BLOCK_TIME: timedelta = timedelta(minutes=int(_opts.get("block_time_minutes", 5)))
# Failed attempts per hour, across all clients, that raise an alert. Never a lockout.
MAX_GLOBAL_ATTEMPTS_PER_HOUR: int = int(
    _opts.get("max_global_attempts_per_hour", 50)
)
SESSION_MAX_ATTEMPTS: int = int(_opts.get("session_max_attempts", 3))

# Admin sessions live in signed cookies that logging out cannot revoke, so the
# server bounds their age itself, counted from the login time signed into them.
ADMIN_SESSION_MAX_AGE = timedelta(hours=12)
ADMIN_REMEMBER_MAX_AGE = timedelta(days=30)

# ---------------------------------------------------------------------------
# Secrets: refuse to start with ones an attacker could guess
# ---------------------------------------------------------------------------
_DEFAULT_PASSWORDS = {
    "", "admin", "admin123", "password", "changeme", "change_me", "change-me",
    "change_me_please", "your_admin_password",
}
_PLACEHOLDER_SECRETS = {
    "secret", "secret_key", "changeme", "change-me", "please-change-me",
    "change-me-to-something-random", "your-secret-key-here",
}
MIN_SECRET_KEY_LENGTH = 16

admin_password: str = (_opts.get("admin_password") or "").strip()
if admin_password.lower() in _DEFAULT_PASSWORDS:
    raise RuntimeError(
        "Refusing to start: admin_password is empty or a well-known default, so anyone "
        "could log in as admin. Set a real password in the add-on configuration."
    )

secret_key: str | None = (
    os.environ.get("FLASK_SECRET_KEY") or (_opts.get("secret_key") or "").strip() or None
)
if secret_key and (len(secret_key) < MIN_SECRET_KEY_LENGTH or secret_key.lower() in _PLACEHOLDER_SECRETS):
    raise RuntimeError(
        f"Refusing to start: secret_key is a placeholder or shorter than {MIN_SECRET_KEY_LENGTH} "
        "characters, so session cookies (admin logins included) could be forged. Leave it empty "
        "for a random key, or generate one: python3 -c 'import secrets; print(secrets.token_hex(32))'"
    )
random_secret_warning: bool = secret_key is None

# ---------------------------------------------------------------------------
# Session cookie security
# ---------------------------------------------------------------------------
_env_secure = os.environ.get("SESSION_COOKIE_SECURE")
if _env_secure is not None:
    session_cookie_secure: bool = _env_secure.lower() == "true"
else:
    session_cookie_secure = bool(_opts.get("session_cookie_secure", False))
