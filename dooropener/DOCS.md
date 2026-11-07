# DoorOpener - Home Assistant Add-on

A secure web interface for controlling smart door openers via Home Assistant.

## Installation

1. Add this repository to your Home Assistant add-on store:
   **Settings -> Add-ons -> Add-on Store -> ⋮ -> Repositories** -> paste:
   ```
   https://github.com/leonardpitzu/DoorOpener
   ```
2. Find **DoorOpener** in the store and click **Install**.
3. Configure the add-on (see below) and click **Start**.

## Configuration

| Option | Description | Required |
|--------|-------------|----------|
| `ha_url` | Home Assistant URL (default: the Supervisor API) | No |
| `ha_token` | Long-lived access token (leave empty to use the Supervisor token) | No |
| `entity_id` | Entity to trigger (`switch.*`, `lock.*`, `input_boolean.*`, or `button.*`) | **Yes** |
| `battery_entity` | Battery sensor entity for monitoring | No |
| `admin_password` | Password for the admin dashboard. The add-on will not start while it is empty or a well-known default | **Yes** |
| `test_mode` | Simulate door actions without calling HA | No |
| `max_attempts` | Failed PIN attempts per IP before blocking | No |
| `block_time_minutes` | Length of the first block; each further block doubles, up to 24 hours | No |
| `session_max_attempts` | Failed attempts per browser session (and admin logins per IP) before blocking | No |
| `max_global_attempts_per_hour` | Failed attempts per hour, across all clients, that raise an alert | No |
| `ssl` | Serve HTTPS with the certificate in `/ssl` (see below) | No |
| `certfile` / `keyfile` | Certificate and key file names in `/ssl` (default: `fullchain.pem` / `privkey.pem`) | No |
| `trusted_proxies` | Reverse proxies in front of DoorOpener (see below); `0` when clients connect directly | No |
| `secret_key` | Session signing key, 16+ characters (random on every start if empty) | No |
| `session_cookie_secure` | Send cookies over HTTPS only; enable behind an HTTPS reverse proxy (implied by `ssl`) | No |

### HTTPS (`ssl`)

Turn on `ssl` and DoorOpener serves HTTPS itself, on the same port, with the
certificate in Home Assistant's `/ssl` folder. With the Let's Encrypt app,
nothing else is needed: `certfile` and `keyfile` default to the
`fullchain.pem` and `privkey.pem` it writes there, and a renewed certificate is
served from the next connection on, without a restart. The watchdog and
**Open Web UI** switch to https by themselves.

Open DoorOpener by the name on the certificate (for example
`https://example.duckdns.org:6532`); plain `http://` links stop working. If the
certificate or key is missing, or they do not match, DoorOpener does not start
and its log says why.

### Reverse proxies (`trusted_proxies`)

Every brute-force limit counts per client IP, so DoorOpener must know the real
one. Leave `trusted_proxies` at `0` when browsers reach port 6532 directly: the
`X-Forwarded-For` header is then ignored, because anyone could forge it. Set it
to `1` when exactly one reverse proxy (NGINX Proxy Manager, Caddy, a Cloudflare
tunnel, ...) sits in front, and only if port 6532 cannot be reached around it.
With `0` behind a proxy, every visitor shares the proxy's IP, so one person's
wrong PINs block everyone.

## Usage

Once started, open the add-on via the sidebar (the door icon) or navigate to
its web UI. Enter your PIN on the visual keypad to open the door.

### Managing Users

Open the admin panel (gear icon), log in with your `admin_password`, and use
the **Users** tab to create, edit, or deactivate users. Each user gets a
unique 4-8 digit PIN; **🎲 Generate** picks one that no other user has and
that nobody would guess first (no `1111`, no `1234`).

Admin logins last 12 hours, or 30 days with **Remember me**.

## Security

- **Brute-force blocks** double with every repeat (5 min, 10 min, 20 min, ...
  up to 24 hours) and reset when the client gets a PIN right.
- **Alerts, not lockouts**: when `max_global_attempts_per_hour` failures pile
  up within an hour, DoorOpener writes it to the audit log and raises a
  persistent notification in Home Assistant. Valid PINs keep working.
- **A disabled user's PIN** is answered like any wrong PIN; the audit log
  records which account it was.

## Data Storage

All add-on data is stored in `/data/` (persisted across restarts):

- `options.json` - configuration (managed by HA)
- `users.json` - user database
- `logs/log.txt` - audit log
