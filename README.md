# DoorOpener for Home Assistant

A secure web interface for controlling smart door openers via [Home Assistant](https://www.home-assistant.io/) - modern glass-morphism UI with visual keypad, per-user PINs, audio feedback, battery monitoring, and brute-force protection.

[![CI](https://github.com/leonardpitzu/DoorOpener/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/leonardpitzu/DoorOpener/actions/workflows/ci.yml)
![Version](https://img.shields.io/badge/version-2.6.0-blue?style=flat-square)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue?style=flat-square)

<img width="2554" height="1187" alt="image" src="https://github.com/user-attachments/assets/e9e2fd6c-aa32-4ea1-933f-668fad3fbfc4" />

<img width="2554" height="1187" alt="image" src="https://github.com/user-attachments/assets/4d5259fa-ee7b-4d03-a02b-b77301cebf0c" />

## Features

DoorOpener provides a web-based keypad interface to remotely open doors connected to Home Assistant. Users enter their personal PIN on a visual keypad and the system securely communicates with Home Assistant to trigger the door opener.

- **Visual keypad** - 3×4 keypad interface with auto-submit
- **Per-user PINs** - individual PINs for each user with JSON-based user management
- **Audio feedback** - success chimes and failure sounds
- **Battery monitoring** - real-time battery level for Zigbee devices
- **Multi-layer security** - rate limiting and IP blocking (see [Security](#security))
- **Admin UI** - user management with a PIN generator, and an audit log viewer
- **Test mode** - safe development without triggering the actual door
- **Entity support** - Home Assistant `switch`, `lock`, `input_boolean`, and `button` entities

## Installation

### Home Assistant Add-on (recommended)

1. In Home Assistant go to **Settings -> Add-ons -> Add-on Store -> ⋮ -> Repositories**.
2. Paste the repository URL:
   ```
   https://github.com/leonardpitzu/DoorOpener
   ```
3. Find **DoorOpener** in the store and click **Install**.
4. Configure `entity_id` and `admin_password` in the add-on options.
5. Click **Start** - the panel appears in the sidebar with a door icon.

> When running as an add-on you can leave `ha_url` and `ha_token` empty - the
> Supervisor API token is used automatically.

### Docker

A `Dockerfile.dev` is provided for local testing outside Home Assistant:

```bash
docker build -f Dockerfile.dev -t dooropener:dev .
cp options.json.example options.json   # edit as needed
docker run -d \
  -v $(pwd)/options.json:/app/options.json:ro \
  -v $(pwd)/users.json:/app/users.json:rw \
  -v $(pwd)/logs:/app/logs \
  -p 6532:6532 dooropener:dev
```

Or via Compose:

```bash
docker compose up -d
```

## Configuration

### `options.json`

All application settings live in a single `options.json` file. Copy the example and edit:

```bash
cp options.json.example options.json
```

```json
{
  "ha_url": "http://homeassistant.local:8123",
  "ha_token": "your_long_lived_access_token_here",
  "entity_id": "switch.dooropener_zigbee",
  "battery_entity": "sensor.dooropener_zigbee_battery",
  "port": 6532,
  "test_mode": false,
  "admin_password": "change_me_please",
  "max_attempts": 5,
  "block_time_minutes": 5,
  "max_global_attempts_per_hour": 50,
  "session_max_attempts": 3,
  "secret_key": "",
  "session_cookie_secure": false,
  "trusted_proxies": 0,
  "ha_cert_pem": ""
}
```

| Key | Description | Default |
|---|---|---|
| `ha_url` | Home Assistant base URL (a trailing `/api` is ignored) | Supervisor API |
| `ha_token` | Long-lived access token (HA -> Profile -> Security) | Supervisor token |
| `entity_id` | Entity to trigger (`switch.*`, `lock.*`, `input_boolean.*`, or `button.*`) | _(required)_ |
| `battery_entity` | Battery sensor entity for monitoring | auto-derived |
| `port` | Web server port (overridden by `DOOROPENER_PORT` env var) | `6532` |
| `test_mode` | When `true`, simulates door actions without calling HA | `false` |
| `admin_password` | Password for the admin dashboard; startup fails while it is empty or a well-known default | _(required)_ |
| `max_attempts` | Failed PIN attempts per IP before blocking | `5` |
| `block_time_minutes` | First block's length; each further block doubles, up to 24 h | `5` |
| `max_global_attempts_per_hour` | Failed attempts per hour, across all clients, that raise an alert (never a lockout) | `50` |
| `session_max_attempts` | Failed attempts per browser session before blocking | `3` |
| `secret_key` | Session signing key, 16+ characters; empty means random per start (or set `FLASK_SECRET_KEY`) | `""` |
| `session_cookie_secure` | Set `true` when running behind HTTPS | `false` |
| `trusted_proxies` | Reverse proxies whose `X-Forwarded-For` is believed; `0` = clients connect directly | `0` |
| `ha_cert_pem` | Inline PEM certificate for self-signed HA (see below) | `""` |

### Environment variables

```bash
# Port (optional, overrides options.json)
DOOROPENER_PORT=6532

# Timezone
TZ=Europe/Amsterdam

# Container user/group mapping (linuxserver-style)
PUID=1000
PGID=1000
UMASK=002

# Flask session secret, 16+ characters (recommended for production):
#   python3 -c 'import secrets; print(secrets.token_hex(32))'
FLASK_SECRET_KEY=

# Set true when behind HTTPS reverse proxy
SESSION_COOKIE_SECURE=true
```

Configuration priority: environment variables > `options.json` defaults.

The image supports `PUID`, `PGID`, and `UMASK` to avoid host-side `chown`. On startup the entrypoint aligns the runtime user/group to those IDs, ensures `/app/logs` is writable, then drops privileges via `gosu`.

### Logs

| Log | Location |
|---|---|
| Door access / audit log | `/app/logs/log.txt` (bind-mount `./logs:/app/logs`) |
| Gunicorn / access log | Container stdout/stderr (`docker logs dooropener`) |

### Self-signed certificates

If your Home Assistant uses a self-signed certificate, paste the PEM certificate directly into the `ha_cert_pem` option.

In `options.json`:

```json
{
  "ha_cert_pem": "-----BEGIN CERTIFICATE-----\nMIIFTjCCA...\n-----END CERTIFICATE-----"
}
```

In the HA add-on YAML config (Settings -> Add-ons -> DoorOpener -> Configuration):

```yaml
ha_cert_pem: |-
  -----BEGIN CERTIFICATE-----
  MIIFTjCCAzagAwIBAgIIAQ3X7arBz...
  -----END CERTIFICATE-----
```

The certificate is written to a temporary file at startup and used for all Home Assistant API calls.

> **Note:** The hostname in `ha_url` must match a Subject Alternative Name in the certificate.

## Usage

1. **Access interface** - visit `http://localhost:6532`.
2. **Enter PIN** - use the visual keypad (4-8 digit PIN).
3. **Auto-submit** - the door opens automatically when a valid-length PIN is entered.
4. **Admin access** - click the gear icon for the admin dashboard.

## Security

### Protection layers

| Layer | Description |
|---|---|
| **IP rate limiting** | Blocks an IP after `max_attempts` failures. Keyed on the IP alone, so changing headers buys nothing |
| **Session rate limiting** | Blocks a browser session after `session_max_attempts` failures |
| **Exponential backoff** | The first block lasts `block_time_minutes`; each repeat doubles it, up to 24 h. A correct PIN resets it |
| **Admin login limiting** | Per-IP, with the same backoff, after `session_max_attempts` wrong passwords |
| **Brute-force alert** | `max_global_attempts_per_hour` failures in an hour raise a Home Assistant notification. Never a lockout, which an attacker could use to shut everyone out |
| **No PIN oracle** | A disabled account's PIN is answered and counted like any wrong PIN |
| **Admin session expiry** | Checked on the server: 12 h, or 30 days with "remember me". Bounds a stolen cookie |
| **Startup checks** | Refuses to start with an empty or default admin password, or a guessable secret key |
| **Audit logging** | Every attempt logged with timestamp, IP, session, user, and result |
| **Input validation** | PINs are 4-8 ASCII digits; request bodies are capped at 64 KiB |
| **Security headers** | CSP with per-request nonces, X-Frame-Options DENY, no-sniff, etc. |

Rate limits trust `X-Forwarded-For` only for the `trusted_proxies` hops you configure, so a client that reaches the port directly cannot pick its own IP.

### Security headers

Every response includes:

- `Content-Security-Policy` - self-only, with a per-request script nonce
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy` - geolocation, camera, microphone disabled
- `Cache-Control: no-store`

## API endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Main keypad interface |
| `POST` | `/open-door` | Validate PIN and trigger door entity |
| `GET` | `/battery` | Battery level JSON (`{"level": 85}`) |
| `GET` | `/health` | Liveness probe for the Supervisor watchdog (`{"status": "ok", "version": ...}`) |
| `GET` | `/admin` | Admin dashboard |
| `POST` | `/admin/auth` | Admin login |
| `GET` | `/admin/check-auth` | Check admin session |
| `POST` | `/admin/logout` | Admin logout |
| `GET` | `/admin/logs` | Retrieve audit logs (admin auth required) |
| `POST` | `/admin/logs/clear` | Clear logs (admin auth required) |
| `GET` | `/admin/users` | List users (admin auth required) |
| `POST` | `/admin/users` | Create user (admin auth required) |
| `PUT` | `/admin/users/<name>` | Update user (admin auth required) |
| `DELETE` | `/admin/users/<name>` | Delete user (admin auth required) |
| `GET` | `/admin/generate-pin?length=6` | A random unused, non-trivial PIN (admin auth required) |

Every error is JSON of the form `{"status": "error", "message": "..."}`.

## Architecture

```
┌────────────────────────────────────────────────────┐
│                      app.py                        │
│           Flask routes & request handling          │
├──────────────┬──────────────┬──────────────────────┤
│ config.py    │ security.py  │ ha_client.py         │
│ options.json │ RateLimiter  │ HAClient             │
│ loader       │ headers      │ (requests.Session)   │
│ timezone     │ validation   │ trigger / battery    │
├──────────────┬──────────────┬──────────────────────┤
│ pins.py      │ audit.py     │ users_store.py       │
│ PIN rules    │ AuditLog     │ UsersStore           │
│ generator    │ JSON lines   │ JSON user database   │
└──────────────┴──────────────┴──────────────────────┘
```

| Module | Responsibility |
|---|---|
| `dooropener/config.py` | Loads `options.json`, exposes all settings as module attributes, timezone handling via `zoneinfo` |
| `dooropener/security.py` | `Throttle` (per-key backoff), `FailureAlarm`, `RateLimiter`, client identity, security headers |
| `dooropener/pins.py` | PIN validation, constant-time comparison, the PIN generator |
| `dooropener/audit.py` | `AuditLog`: writes, reads back and clears the audit trail |
| `dooropener/ha_client.py` | `HAClient` class wrapping `requests.Session` - dispatches `switch`/`lock`/`input_boolean`/`button` services, sends alerts |
| `dooropener/users_store.py` | Atomic JSON-based user CRUD with usage tracking |
| `dooropener/app.py` | Flask app, route handlers, audit logging |

## Development

### Prerequisites

- Python 3.12+
- An `options.json` file (copy from `options.json.example`)

### Local setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp options.json.example options.json   # edit as needed
python app.py
```

### Running tests

```bash
pytest --tb=short -q
```

With coverage:

```bash
pytest --cov=./ --cov-report=term-missing --cov-fail-under=75
```

### Linting & security

```bash
ruff check .          # lint
bandit -r . -x tests  # security scan
```

### Test mode

Set `"test_mode": true` in `options.json` to test the interface without actually triggering the door in Home Assistant.

## CI / CD

The CI pipeline (`.github/workflows/ci.yml`) runs on every push and PR:

| Job | Tool | Purpose |
|---|---|---|
| **Tests** | pytest + pytest-cov | Unit/integration tests, 75 % coverage gate |
| **Lint** | ruff | Code style and import checks |
| **Security** | bandit | Static security analysis |
| **Docker** | docker build | Smoke-test the dev container image |

Monthly pull requests for dependency updates are configured via `.github/dependabot.yml`:

- **pip** dependencies
- **GitHub Actions** versions

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
