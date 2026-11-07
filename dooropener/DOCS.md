# DoorOpener - Home Assistant App

A PIN keypad for your door, opened through Home Assistant.

## Installation

1. Add this repository to your Home Assistant app store:
   **Settings -> Apps -> App Store -> ⋮ -> Repositories** -> paste:
   ```
   https://github.com/leonardpitzu/DoorOpener
   ```
2. Find **DoorOpener** in the store and click **Install**.
3. Set `entity_id` (see below) and click **Start**.

The keypad is on port 6532. The admin panel is in Home Assistant's sidebar
(the door icon).

## Configuration

| Option | Description | Required |
|--------|-------------|----------|
| `entity_id` | Entity to trigger (`switch.*`, `lock.*`, `input_boolean.*`, or `button.*`) | **Yes** |
| `battery_entity` | Battery sensor entity for monitoring | No |
| `ha_url` | Home Assistant URL. Leave the default to use the app's own connection | No |
| `ha_token` | Long-lived access token. Leave empty to use the app's own token | No |
| `admin_password` | Also offer the admin panel on port 6532, behind this password (see below). Empty: only in the sidebar | No |
| `ssl` | Serve HTTPS with the certificate in `/ssl` (see below) | No |
| `certfile` / `keyfile` | Certificate and key file names in `/ssl` (default: `fullchain.pem` / `privkey.pem`) | No |
| `test_mode` | Simulate door actions without calling HA | No |
| `max_attempts` | Failed PIN attempts per IP before blocking | No |
| `block_time_minutes` | Length of the first block; each further block doubles, up to 24 hours | No |
| `session_max_attempts` | Failed attempts per browser session (and admin logins per IP) before blocking | No |
| `max_global_attempts_per_hour` | Failed attempts per hour, across all clients, that raise an alert | No |
| `trusted_proxies` | Reverse proxies in front of DoorOpener (see below); `0` when clients connect directly | No |
| `secret_key` | Session signing key, 16+ characters (random on every start if empty) | No |
| `session_cookie_secure` | Send cookies over HTTPS only; enable behind an HTTPS reverse proxy (implied by `ssl`) | No |

### The admin panel

Open **DoorOpener** in Home Assistant's sidebar. Home Assistant has already
logged you in, with its two-factor step if you use one, so there is no password:
any Home Assistant administrator gets in, and the audit log records who. This
works wherever your Home Assistant app works, at home or away.

With `admin_password` empty (the default), port 6532 serves only the keypad,
and nobody on the internet can try an admin password at all. Set a password to
also offer the panel there (the gear icon on the keypad).

### Users and guest PINs

In the admin panel's **Users** tab, each user gets a unique 4-8 digit PIN;
**🎲 Generate** picks one that no other user has and that nobody would guess
first (no `1111`, no `1234`). Under **Limit when this PIN works** a PIN can be
limited to:

- **dates**: valid from / until, for a guest's stay;
- **days and hours**, for a cleaner (Mon-Fri 08:00-18:00). Hours like 22:00 to
  06:00 run overnight;
- **one use**, for a courier: the PIN is deactivated after the first opening.

Times are the door's local time. Outside its window a PIN is answered like any
wrong one; the audit log says whose it was and why.

PINs are stored hashed, so nobody can read them back: not from the admin panel,
not from a backup made from now on. Pass a PIN on when you create it. Plain-text
PINs from versions before 2.8.0 are converted on the first start.

### Home Assistant events

Every opening fires a `dooropener_door_opened` event with the user's name
(`user`) and the door entity (`entity_id`), and adds a logbook entry ("opened
by alice"). Use the event in automations, for example to be told when a guest
arrives. Test mode sends neither.

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

With `ssl` on, a client must finish each request within 15 seconds, so a few
deliberately slow connections cannot freeze the keypad.

### Reverse proxies (`trusted_proxies`)

Every brute-force limit counts per client IP, so DoorOpener must know the real
one. Leave `trusted_proxies` at `0` when browsers reach port 6532 directly: the
`X-Forwarded-For` header is then ignored, because anyone could forge it. Set it
to `1` when exactly one reverse proxy (NGINX Proxy Manager, Caddy, a Cloudflare
tunnel, ...) sits in front, and only if port 6532 cannot be reached around it.
With `0` behind a proxy, every visitor shares the proxy's IP, so one person's
wrong PINs block everyone.

## Security

- **Brute-force blocks** double with every repeat (5 min, 10 min, 20 min, ...
  up to 24 hours) and reset when the client gets a PIN right.
- **Alerts, not lockouts**: when `max_global_attempts_per_hour` failures pile
  up within an hour, DoorOpener writes it to the audit log and raises a
  persistent notification (id `dooropener_alert`) in Home Assistant. Valid PINs
  keep working.
- **No PIN oracle**: a disabled PIN, or one outside its window, is answered like
  any wrong PIN; only the audit log names the account.

## Data Storage

All app data is stored in `/data/` (persisted across restarts):

- `options.json` - configuration (managed by HA)
- `users.json` - users, with hashed PINs
- `logs/log.txt` - audit log
