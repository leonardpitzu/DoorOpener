# DoorOpener for Home Assistant

A secure web keypad for the doors, barriers and gates your [Home Assistant](https://www.home-assistant.io/) opens - modern glass-morphism UI with visual keypad, per-user PINs with their own doors and hours, guests who switch themselves off, audio feedback, battery monitoring, and brute-force protection.

[![CI](https://github.com/leonardpitzu/DoorOpener/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/leonardpitzu/DoorOpener/actions/workflows/ci.yml)
![Version](https://img.shields.io/badge/version-3.0.0-blue?style=flat-square)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue?style=flat-square)

<img width="2554" height="1187" alt="image" src="https://github.com/user-attachments/assets/e9e2fd6c-aa32-4ea1-933f-668fad3fbfc4" />

<img width="2554" height="1187" alt="image" src="https://github.com/user-attachments/assets/4d5259fa-ee7b-4d03-a02b-b77301cebf0c" />

## Features

DoorOpener provides a web-based keypad interface to remotely open doors connected to Home Assistant. Users enter their personal PIN on a visual keypad and the system securely communicates with Home Assistant to trigger the door opener.

- **Visual keypad** - 3×4 keypad interface with auto-submit
- **Several doors** - a door, a barrier, a gate: a PIN that may open one opens it, one that may open several shows a tile for each
- **Shortcut links** - `/barrier`, `/barrier+door`: the same link for everyone (a QR code at the barrier, a home-screen icon), and the PIN decides
- **Per-user PINs** - individual PINs, stored hashed, each with the doors it may open
- **Schedules** - as many windows of days and hours as needed (school mornings, afternoons, weekends)
- **Guests** - an end date, optionally a number of uses (a visit counts once: barrier, door, barrier out), and switched off by themselves afterwards
- **Plain answers for the family** - a refused PIN looks like any wrong one, except on its owner's own phone: "Not now. You can open again tomorrow at 07:00."
- **No names on screen** - the keypad greets by the time of day, never by whose PIN it was
- **Audio feedback** - success chimes and failure sounds
- **Battery monitoring** - a battery level per door
- **Multi-layer security** - rate limiting and IP blocking (see [Security](#security))
- **Admin UI** - user management with a PIN generator, and an audit log viewer; inside Home Assistant, behind its login
- **Home Assistant events** - openings fire `dooropener_door_opened` (who, which door), refusals of someone's own PIN `dooropener_access_refused`; both can be switched off
- **HTTPS** - served by DoorOpener itself; renewed certificates are picked up without a restart
- **Test mode** - safe development without triggering the actual door
- **Entity support** - Home Assistant `switch`, `lock`, `input_boolean`, `button`, `cover` and `script` entities

## Installation

### Home Assistant Add-on (recommended)

1. In Home Assistant go to **Settings -> Add-ons -> Add-on Store -> ⋮ -> Repositories**.
2. Paste the repository URL:
   ```
   https://github.com/leonardpitzu/DoorOpener
   ```
3. Find **DoorOpener** in the store and click **Install**.
4. Add your doors on the app's configuration page.
5. Click **Start**. The keypad is on port 6532; the admin panel opens with
   **Open Web UI**, behind your Home Assistant login.

> As an app, DoorOpener talks to Home Assistant through the Supervisor and keeps
> its session key in `/data`, so its configuration page has no URL, token, port
> or key to fill in. Open the admin panel with **Open Web UI** on the app's page,
> or switch on **Show in sidebar** there. `admin_password` is optional; set it
> only to also offer the admin panel on port 6532. See the app's documentation tab.

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

To serve HTTPS, see [Choose a setup](#choose-a-setup).

Standalone, DoorOpener reaches Home Assistant with `ha_url` and a long-lived
`ha_token`, and the admin panel is on its own port behind `admin_password`. With
no Home Assistant configured it still runs: the keypad and the admin panel work,
only opening a door fails (and says why).

## Configuration

### `options.json`

All application settings live in a single `options.json` file, laid out like the
app's configuration page: the doors first, then DoorOpener's own settings, then
what only a standalone container needs. Copy the example and edit:

```bash
cp options.json.example options.json
```

```json
{
  "doors": [
    {"name": "Barrier", "id": "barrier", "entity_id": "cover.parking_barrier", "icon": "barrier"},
    {"name": "Door", "id": "door", "entity_id": "switch.dooropener_zigbee",
     "battery_entity": "sensor.dooropener_zigbee_battery"}
  ],
  "settings": {
    "ssl": false,
    "certfile": "fullchain.pem",
    "keyfile": "privkey.pem",
    "trusted_proxies": 0,
    "admin_password": "change_me_please",
    "report_to_ha": true,
    "max_attempts": 5,
    "session_max_attempts": 3,
    "block_time_minutes": 5,
    "max_global_attempts_per_hour": 50,
    "test_mode": false
  },
  "ha_url": "http://homeassistant.local:8123",
  "ha_token": "your_long_lived_access_token_here",
  "ha_cert_pem": "",
  "port": 6532,
  "secret_key": ""
}
```

A flat `options.json` from before 2.10, with the settings at the top level,
works the same, and so does one from before 3.0 with a single `entity_id` (and
`battery_entity`) at the top level: it is one door, `door`, called Door.

`doors`, in the order the keypad shows them:

| Key | Description | Default |
|---|---|---|
| `name` | Shown on the keypad, e.g. `Barrier` | _(required)_ |
| `id` | Lowercase letters, digits, `-` and `_`: the shortcut link (`/barrier`) and what users' door ticks refer to. Keep it once users have the door | _(required)_ |
| `entity_id` | What opens it: `switch.*`, `lock.*`, `input_boolean.*`, `button.*`, `cover.*` (`open_cover`) or `script.*` | _(required)_ |
| `battery_entity` | Battery sensor (`sensor.*`) shown on the keypad | `""` |
| `icon` | `door`, `barrier`, `gate` or `garage` | `door` |

`settings`:

| Key | Description | Default |
|---|---|---|
| `ssl` | Serve HTTPS with the certificate below (see [Choose a setup](#choose-a-setup)) | `false` |
| `certfile` | Certificate with its full chain: a file name in `/ssl`, or a full path | `fullchain.pem` |
| `keyfile` | The certificate's private key: a file name in `/ssl`, or a full path | `privkey.pem` |
| `trusted_proxies` | Reverse proxies in front; their `X-Forwarded-For` is believed only from your own network. `0` = clients connect directly | `0` |
| `admin_password` | Password for the admin panel on DoorOpener's own port; startup fails while it is a well-known default, or empty outside an app | _(required standalone)_ |
| `report_to_ha` | Send openings and refusals of someone's own PIN to Home Assistant as events and logbook entries. The brute-force alert is sent either way | `true` |
| `max_attempts` | Failed PIN attempts per IP before blocking | `5` |
| `session_max_attempts` | Failed attempts per browser session before blocking | `3` |
| `block_time_minutes` | First block's length; each further block doubles, up to 24 h | `5` |
| `max_global_attempts_per_hour` | Failed attempts per hour, across all clients, that raise an alert (never a lockout) | `50` |
| `test_mode` | When `true`, simulates door actions without calling HA | `false` |

Standalone only (an app has the Supervisor for these):

| Key | Description | Default |
|---|---|---|
| `ha_url` | Home Assistant base URL (a trailing `/api` is ignored) | Supervisor API |
| `ha_token` | Long-lived access token (HA -> Profile -> Security) | Supervisor token |
| `ha_cert_pem` | Inline PEM certificate for a Home Assistant with a self-signed one (see below) | `""` |
| `port` | Web server port (overridden by `DOOROPENER_PORT` env var) | `6532` |
| `secret_key` | Session signing key, 16+ characters (or set `FLASK_SECRET_KEY`); empty means one kept in `DOOROPENER_SECRET_KEY_FILE`, or a random one per start | `""` |

### Environment variables

```bash
# Port (optional, overrides options.json)
DOOROPENER_PORT=6532

# Timezone
TZ=Europe/Amsterdam

# Folder where certfile/keyfile names without a directory are looked up
DOOROPENER_SSL_DIR=/ssl

# Flask session secret, 16+ characters (recommended for production):
#   python3 -c 'import secrets; print(secrets.token_hex(32))'
FLASK_SECRET_KEY=

# Without a secret_key: a persistent file DoorOpener keeps a generated key in
# (an app uses /data/secret_key)
DOOROPENER_SECRET_KEY_FILE=/app/logs/secret_key
```

Configuration priority: environment variables > `options.json` defaults.

### Logs

| Log | Location |
|---|---|
| Door access / audit log | `/app/logs/log.txt` (bind-mount `./logs:/app/logs`). Circular: the last 180 days, at most 5 MB; the oldest entries make room for new ones |
| Gunicorn / access log | Container stdout/stderr (`docker logs dooropener`) |

### Choose a setup

How guests reach the keypad decides two settings, `ssl` and `trusted_proxies`.
It is the same as a Home Assistant app and standalone; only where the
certificate comes from differs.

| Setup | `ssl` | `trusted_proxies` | Keypad address |
|-------|-------|-------------------|----------------|
| **A. DoorOpener serves HTTPS** (recommended when the keypad is reachable from the internet) | `true` | `0` | `https://door.example.com:6532` |
| **B. Behind a reverse proxy or tunnel that does HTTPS** (NGINX Proxy Manager, Cloudflare tunnel, Caddy, Traefik, ...) | `false` | `1` | the proxy's address |
| **C. Plain HTTP, home network only** | `false` | `0` | `http://<host>:6532` |

**A.** DoorOpener serves HTTPS on its usual port; `certfile` and `keyfile`
name the certificate (with its full chain) and its private key. A bare file name
is looked up in `/ssl`; a full path is used as given.

- **Home Assistant app**: turn on **HTTPS**. With the Let's Encrypt app (or the
  DuckDNS app, without a domain of your own) that is all: it writes
  `fullchain.pem` and `privkey.pem` to `/ssl`, which are the defaults.
- **Docker**: put the two files in `./ssl` and uncomment the `./ssl:/ssl:ro`
  line in `docker-compose.yml`. Or mount your certbot folder and give full paths:

  ```bash
  -v /etc/letsencrypt:/etc/letsencrypt:ro
  ```

  ```json
  "settings": {
    "ssl": true,
    "certfile": "/etc/letsencrypt/live/door.example.com/fullchain.pem",
    "keyfile": "/etc/letsencrypt/live/door.example.com/privkey.pem"
  }
  ```

  Mount all of `/etc/letsencrypt`: the files in `live/` are links into `archive/`.

Renewals need nothing. Every new connection gets the certificate as it is on
disk, so a renewed one is served at once, without a restart. A renewal caught
halfway (new certificate, old key) keeps the previous certificate until both
files are in place. Open DoorOpener by the name on its certificate: an IP
address gets a certificate warning, and plain `http://` no longer answers.
DoorOpener refuses to start while the certificate or key is missing, or the two
do not belong together, and the log says which files it tried. A client must
finish each request within 15 seconds, so slow connections cannot hold the
server's threads.

**B.** The proxy does HTTPS and buffers requests, so slow clients are its
problem. With `trusted_proxies` at `1`, the brute-force limits see each
visitor's own address, which DoorOpener takes from the proxy's
`X-Forwarded-For`, and only when the connection comes from a private, loopback
or link-local address, where your proxy runs: a client on the internet that
reaches the port around the proxy cannot pick its own address. With `0` behind
a proxy, every visitor shares the proxy's address, so one person's wrong PINs
block everyone. Close the port to the internet if you can.

**C.** Works out of the box, but PINs travel unencrypted: do not forward the
port to the internet this way.

The session cookie follows the setup by itself: HTTPS-only whenever the request
came over HTTPS (A, and B through the proxy's `X-Forwarded-Proto`), a normal
cookie over plain HTTP (C), where an HTTPS-only one would never come back and
the keypad could not open the door. There is nothing to configure for it.

### Home Assistant with a self-signed certificate

Standalone only: an app reaches Home Assistant through the Supervisor. If the
`ha_url` you give uses HTTPS with a self-signed or private-CA certificate, paste
that certificate (PEM) into the `ha_cert_pem` option in `options.json`:

```json
{
  "ha_cert_pem": "-----BEGIN CERTIFICATE-----\nMIIFTjCCA...\n-----END CERTIFICATE-----"
}
```

The certificate is written to a temporary file at startup and used for all Home Assistant API calls.

> **Note:** The hostname in `ha_url` must match a Subject Alternative Name in the certificate.

## Usage

1. **Access interface** - visit `http://localhost:6532` (`https://` and the certificate's name with `ssl` on).
2. **Enter PIN** - use the visual keypad (4-8 digit PIN).
3. **Auto-submit** - when a valid-length PIN is entered, its door opens; a PIN for several doors shows a tile for each, for 30 seconds.
4. **Shortcuts** - `/barrier` or `/barrier+door` opens straight to those doors: the PIN still decides which of them it may open. Each shortcut can go on the home screen as an icon of its own.
5. **Admin access** - **Open Web UI** on the app's page (or its sidebar entry), or the gear icon on the keypad when `admin_password` is set.

## Security

### Protection layers

| Layer | Description |
|---|---|
| **IP rate limiting** | Blocks an IP after `max_attempts` failures. Keyed on the IP alone, so changing headers buys nothing |
| **Session rate limiting** | Blocks a browser session after `session_max_attempts` failures |
| **Exponential backoff** | The first block lasts `block_time_minutes`; each repeat doubles it, up to 24 h. A correct PIN resets it |
| **Admin login limiting** | Per-IP, with the same backoff, after `session_max_attempts` wrong passwords |
| **Brute-force alert** | `max_global_attempts_per_hour` failures in an hour raise a Home Assistant notification. Never a lockout, which an attacker could use to shut everyone out |
| **No PIN oracle** | A disabled PIN, one outside its window, or one for another door is answered and counted like any wrong PIN. Only a phone that has opened with that same user's PIN before, holding a signed cookie that a new PIN invalidates, is told why |
| **Hashed PINs** | `users.json` holds scrypt hashes, never the PINs; older plain-text stores are converted on load |
| **Admin in Home Assistant** | In an app, the admin panel is reached through ingress behind the Home Assistant login; without `admin_password` it is not on the public port at all |
| **Admin session expiry** | Checked on the server: 12 h, or 30 days with "remember me". Bounds a stolen cookie |
| **Startup checks** | Refuses to start with an empty or default admin password, a guessable secret key, or `ssl` on without a usable certificate |
| **Audit logging** | Every attempt logged with timestamp, IP, session, user, and result |
| **HTTPS** | Served by DoorOpener itself with `ssl`; renewed certificates are picked up without a restart |
| **Slow clients** | With `ssl`, a client gets 15 s for each request, so slow connections cannot hold the server's threads |
| **Input validation** | PINs are 4-8 ASCII digits; request bodies are capped at 64 KiB |
| **Security headers** | CSP with scripts from DoorOpener only, X-Frame-Options DENY (SAMEORIGIN for Home Assistant's frame), no-sniff, etc. |

Rate limits trust `X-Forwarded-For` only for the `trusted_proxies` hops you configure, and only from a proxy on your own network, so a client that reaches the port directly cannot pick its own IP.

### Security headers

Every response includes:

- `Content-Security-Policy` - self-only; scripts are static files, never inline
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY` (`SAMEORIGIN` for the admin pages Home Assistant shows in its own frame)
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy` - geolocation, camera, microphone disabled
- `Cache-Control: no-store`

## API endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Main keypad interface |
| `GET` | `/<door>[+<door>...]` | A shortcut page for those doors |
| `POST` | `/open-door` | `{"pin": ..., "doors": [...]}` (`doors` from a shortcut page only). One door: opened, `{"status": "success", "door": ...}`. Several: `{"status": "choose", "pass": ..., "doors": [...]}` |
| `POST` | `/open-door/choice` | `{"pass": ..., "door": ...}`: the tile picked, within 30 s, from the same browser |
| `GET` | `/battery?door=<id>` | A door's battery level (`{"level": 85}`) |
| `GET` | `/manifest.webmanifest[?to=<doors>]` | The web app manifest; a shortcut's own with `to` |
| `GET` | `/health` | Liveness probe for the Supervisor watchdog (`{"status": "ok", "version": ...}`) |
| `GET` | `/admin` | Admin dashboard |
| `POST` | `/admin/auth` | Admin login |
| `GET` | `/admin/check-auth` | Check admin session |
| `POST` | `/admin/logout` | Admin logout |
| `GET` | `/admin/logs` | Retrieve audit logs (admin auth required) |
| `POST` | `/admin/logs/clear` | Clear logs (admin auth required) |
| `GET` | `/admin/users` | List users, with their `doors` and `access` rules, and the configured `doors` (admin auth required) |
| `POST` | `/admin/users` | Create user: `username`, `pin`, `doors`, optional `access` (admin auth required) |
| `PUT` | `/admin/users/<name>` | Update user: any of `pin`, `active`, `doors`, `access` (`null` clears it) (admin auth required) |
| `DELETE` | `/admin/users/<name>` | Delete user (admin auth required) |
| `GET` | `/admin/generate-pin?length=6` | A random unused, non-trivial PIN (admin auth required) |

Every error is JSON of the form `{"status": "error", "message": "..."}`.

`access` is `null` (the PIN always works) or an object with any of
`valid_from` / `valid_until` (local time, `2026-11-10T08:00`; an end date makes
the user a guest, switched off once it passes), `uses` (a guest's number of
visits; openings within 15 minutes count once) and `schedule`: a list of
`{"days": [0, 1, 2, 3, 4], "hours": ["07:00", "20:00"]}` rows, any of which
lets the PIN in (Monday = 0; `null` days: every day; `null` hours: all day; a
later start than end runs overnight). A guest needs `valid_until`.

Through Home Assistant's ingress the admin endpoints need no login: Home
Assistant has authenticated the user, and only its administrators get in.

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
| `dooropener/gunicorn.conf.py` | Server settings (port, HTTPS, the single worker), shared by the app and the container |
| `dooropener/tls.py` | `Certificate`: the HTTPS certificate, reloaded when its files change; deadlines for slow clients |
| `dooropener/security.py` | `Throttle` (per-key backoff), `FailureAlarm`, `RateLimiter`, client identity, security headers |
| `dooropener/pins.py` | PIN validation, hashing, constant-time comparison, the PIN generator |
| `dooropener/access.py` | When a PIN works: a guest's dates and uses (counted per visit), schedules of days and hours |
| `dooropener/passes.py` | The 30-second pass between a PIN for several doors and the tile picked |
| `dooropener/phones.py` | The signed cookie that recognises a user's own phone, for plain refusals |
| `dooropener/ingress.py` | The admin panel inside Home Assistant: the ingress listener (apps only) |
| `dooropener/audit.py` | `AuditLog`: the circular audit trail (180 days, 5 MB): writes, reads back, trims and clears |
| `dooropener/ha_client.py` | `HAClient` class wrapping `requests.Session` - dispatches `switch`/`lock`/`input_boolean`/`button`/`cover`/`script` services; best-effort alerts, opening and refusal events and logbook entries, skipped without Home Assistant |
| `dooropener/users_store.py` | Atomic JSON-based user CRUD with hashed PINs, doors, access rules, visits, and guests switched off when done |
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
