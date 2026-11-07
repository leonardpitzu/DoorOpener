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

The keypad is on port 6532. The admin panel opens with **Open Web UI** on the
app's page; switch on **Show in sidebar** there for a sidebar entry too.

## Configuration

The app talks to Home Assistant through the Supervisor and keeps its session
key in `/data`, so there is no URL, token, port or key to fill in. The host port
is in the **Network** section.

| Option | What it does |
|--------|--------------|
| **Door entity** | The switch, lock, input_boolean or button that opens the door, e.g. `button.intercom_open`. Required |
| **Battery sensor (optional)** | A sensor with the opener's battery level in %, shown on the keypad. Empty if it has none |

**DoorOpener settings** (a section of its own):

| Option | What it does |
|--------|--------------|
| **HTTPS** | Serve the keypad over HTTPS with a certificate from `/ssl` (see [Choose a setup](#choose-a-setup)) |
| **Certificate file (file name in /ssl)** | Default `fullchain.pem` |
| **Private key file (file name in /ssl)** | Default `privkey.pem` |
| **Reverse proxies in front** | `0` when browsers connect directly, `1` behind a proxy or tunnel (see [Choose a setup](#choose-a-setup)) |
| **Admin password on the keypad port (optional)** | Also offer the admin panel on port 6532, behind this password (see below) |
| **Wrong PINs per device** | Wrong PINs from one IP address before it is blocked |
| **Wrong PINs per browser** | Wrong PINs from one browser before it is blocked; also the wrong admin passwords allowed per IP address |
| **First block (minutes)** | How long the first block lasts. Each further block doubles, up to 24 hours; a correct PIN resets it |
| **Alert threshold per hour** | Wrong PINs per hour, from everyone together, that raise a brute-force alert. Never a lockout |
| **Test mode** | Go through the motions without opening the door |

### Choose a setup

How guests reach the keypad decides two settings. The admin panel is not
affected: it always opens inside Home Assistant, however you reach that.

| Setup | HTTPS | Reverse proxies in front | Keypad address |
|-------|-------|--------------------------|----------------|
| **A. DoorOpener serves HTTPS** (recommended when the keypad is reachable from the internet) | on | `0` | `https://your.domain:6532` |
| **B. Behind a reverse proxy or tunnel that does HTTPS**: NGINX Proxy Manager, the Cloudflared app, Caddy, ... | off | `1` | the proxy's address |
| **C. Plain HTTP, home network only** | off | `0` | `http://homeassistant.local:6532` |

- **A** needs a certificate in `/ssl`: the Let's Encrypt app, or the DuckDNS app
  if you have no domain of your own. Renewals are picked up without a restart.
  Open the keypad by the name on the certificate; plain `http://` and IP
  addresses stop working. If the certificate or key is missing, or they do not
  match, DoorOpener does not start and its log says why.
- **B** gets HTTPS from the proxy. With `1`, the brute-force limits see each
  visitor's own address, which DoorOpener takes from the proxy's
  `X-Forwarded-For`, and only from a proxy on your network: a client on the
  internet that reaches port 6532 around the proxy cannot pick its own address.
  With `0` behind a proxy, every visitor shares the proxy's address, so one
  person's wrong PINs block everyone. Close port 6532 to the internet if you can.
- **C** works out of the box, but PINs travel unencrypted: do not forward the
  port to the internet this way.

The session cookie follows the setup by itself: HTTPS-only whenever the keypad
is reached over HTTPS (A, B), a normal cookie over plain HTTP (C), where an
HTTPS-only one would never come back and the keypad could not open the door.
Through Home Assistant, the admin panel uses no cookie at all.

In A, a client must finish each request within 15 seconds, so a few
deliberately slow connections cannot freeze the keypad; in B the proxy takes
care of that.

### The admin panel

Open DoorOpener's web UI from Home Assistant (**Open Web UI**, or the sidebar
entry). Home Assistant has already logged you in, with its two-factor step if
you use one, so there is no password: any Home Assistant administrator gets in,
and the audit log records who. This works wherever your Home Assistant app
works, at home or away.

Without an admin password (the default), port 6532 serves only the keypad, and
nobody on the internet can try an admin password at all. Set one to also offer
the panel there (the gear icon on the keypad).

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

## Security

- **Brute-force blocks** double with every repeat (5 min, 10 min, 20 min, ...
  up to 24 hours) and reset when the client gets a PIN right.
- **Alerts, not lockouts**: when **Alert threshold per hour** failures pile
  up within an hour, DoorOpener writes it to the audit log and raises a
  persistent notification (id `dooropener_alert`) in Home Assistant. Valid PINs
  keep working.
- **No PIN oracle**: a disabled PIN, or one outside its window, is answered like
  any wrong PIN; only the audit log names the account.

## Data Storage

All app data is stored in `/data/` (persisted across restarts):

- `options.json` - configuration (managed by HA)
- `users.json` - users, with hashed PINs
- `secret_key` - the session signing key, generated on the first start
- `logs/log.txt` - audit log

## Upgrading

- **2.10**: DoorOpener's own settings moved into the **DoorOpener settings**
  section. Home Assistant does not carry values across such a move, so check
  them once after updating.
