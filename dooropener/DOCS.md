# DoorOpener - Home Assistant App

A PIN keypad for your doors, barriers and gates, opened through Home Assistant.

## Installation

1. Add this repository to your Home Assistant app store:
   **Settings -> Apps -> App Store -> ⋮ -> Repositories** -> paste:
   ```
   https://github.com/leonardpitzu/DoorOpener
   ```
2. Find **DoorOpener** in the store and click **Install**.
3. Add your doors on the **Configuration** tab (see below) and click **Start**.

The keypad is on port 6532. The admin panel opens with **Open Web UI** on the
app's page; switch on **Show in sidebar** there for a sidebar entry too.

## Configuration

The app talks to Home Assistant through the Supervisor and keeps its session
key in `/data`, so there is no URL, token, port or key to fill in. The host port
is in the **Network** section.

**Doors** is a list: add one entry per door, barrier or gate. Each has:

| Field | What it does |
|-------|--------------|
| **Name** | Shown on the keypad, e.g. `Barrier` or `Front door` |
| **Link name** | Lowercase letters, digits, `-` and `_`, e.g. `barrier`. Gives the door a shortcut link (`https://your.domain:6532/barrier`), and is how users' door ticks refer to it: keep it once users have the door |
| **Entity** | What opens it: a switch, lock, input_boolean, button, cover (opened with `open_cover`) or script, e.g. `button.intercom_open` |
| **Battery sensor (optional)** | A sensor with the opener's battery level in %, shown on the keypad |
| **Icon (optional)** | `door`, `barrier`, `gate` or `garage` |

The keypad shows the doors in this order; drag entries to reorder them.

**DoorOpener settings** (a section of its own):

| Option | What it does |
|--------|--------------|
| **HTTPS** | Serve the keypad over HTTPS with a certificate from `/ssl` (see [Choose a setup](#choose-a-setup)) |
| **Certificate file (file name in /ssl)** | Default `fullchain.pem` |
| **Private key file (file name in /ssl)** | Default `privkey.pem` |
| **Reverse proxies in front** | `0` when browsers connect directly, `1` behind a proxy or tunnel (see [Choose a setup](#choose-a-setup)) |
| **Admin password on the keypad port (optional)** | Also offer the admin panel on port 6532, behind this password: at least 12 characters (see below) |
| **Report to Home Assistant** | Openings and refusals of someone's own PIN as events and logbook entries (see [Home Assistant events](#home-assistant-events)). The brute-force alert is sent either way |
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

### The keypad, and shortcut links

Enter a PIN on the keypad: it goes through by itself once you stop typing, and
the PIN box turns green, red or amber with the result. A PIN that may open one
door opens it at once; one that may open several shows a tile for each, for 30
seconds: tap the one to open. Each PIN entry opens one door, so the barrier and
then the door means entering the PIN twice.

Every door also has a shortcut link: `/barrier`, or `/barrier+door` for two.
The link is the same for everyone (put it on a QR code at the barrier, or on
your home screen, where each shortcut gets an icon of its own); the PIN decides
which of its doors it may open.

The keypad never shows whose PIN it was: it says good morning, afternoon or
evening by the phone's clock. Names are in the admin panel and the audit log.

### Users and guests

In the admin panel's **Users** tab, each user gets a unique 4-8 digit PIN;
**🎲 Generate** picks one that no other user has and that nobody would guess
first (no `1111`, no `1234`). Then:

- **May open**: tick the doors this user may open. A door added to the
  configuration later is opened by nobody until you tick it.
- **Full-time or guest.** A guest has an end date (**Until**, with buttons for
  the end of today, of day 3 and of day 7), and may have a start and a number of
  **uses**. When the end date passes or the uses run out, the guest is switched
  off by themselves: still in the list, marked expired or used up, so the next
  visit is a tick and a new end date away.
- **One use is one visit**: the first opening starts a visit of 15 minutes, and
  openings within it count once (barrier in, door, barrier out). A visit also
  outlasts the end of a time window, so someone who came through the barrier at
  13:29 can still open the door at 13:31.
- **When**: **＋ Add a time window** as often as needed, each a set of days and
  hours (kids: Mon-Fri 07:00-08:30 and 13:00-20:00, Sat-Sun 09:00-21:00). No
  windows: any time. Hours like 22:00 to 06:00 run overnight.

Examples: a friend staying three days is a guest until the end of day 3, any
number of uses. A courier is a guest until Monday, 1 use, Monday 10:00-13:30.

Times are the door's local time. Outside its window, a PIN is answered like any
wrong one, so a stranger cannot tell it exists, with one exception: a phone that
has opened a door with that same user's PIN before is told why ("Not now. You
can open again tomorrow at 07:00.", "This PIN expired on Sun 9 Nov.", "This PIN
can't open Barrier."), and those tries do not count as wrong ones. A new PIN
forgets the phones that knew the old one. The audit log always says whose PIN
it was and why.

PINs are stored hashed, so nobody can read them back: not from the admin panel,
not from a backup made from now on. Pass a PIN on when you create it. Plain-text
PINs from versions before 2.8.0 are converted on the first start.

### Home Assistant events

With **Report to Home Assistant** on (the default):

- Every opening fires `dooropener_door_opened` with the user (`user`), the door
  (`door`, `door_name`) and its entity (`entity_id`), and adds a logbook entry
  ("Barrier opened by alice").
- A refusal of someone's own PIN fires `dooropener_access_refused` with `user`,
  `reason` (`outside_schedule`, `expired`, `used_up`, `not_yet_valid`,
  `not_allowed`, `disabled`) and the `doors` tried (empty from the main link).
  A PIN that matches nobody is the brute-force alert's business instead.

Use them in automations, for example to be told when the kids get home or when
the cleaner arrives. Test mode sends neither.

## Security

- **Brute-force blocks** double with every repeat (5 min, 10 min, 20 min, ...
  up to 24 hours) and reset when the client gets a PIN right.
- **Alerts, not lockouts**: when **Alert threshold per hour** failures pile
  up within an hour, DoorOpener writes it to the audit log and raises a
  persistent notification (id `dooropener_alert`) in Home Assistant. Valid PINs
  keep working.
- **No PIN oracle**: a disabled PIN, one outside its window, or one for another
  door is answered like any wrong PIN; only the audit log names the account,
  and only its owner's own phone is told why.

## Data Storage

All app data is stored in `/data/` (persisted across restarts):

- `options.json` - configuration (managed by HA)
- `users.json` - users, with hashed PINs
- `pin_key` - the key the PIN hashes are made with, created on the first
  start. A copy of `users.json` without it cannot be searched for PINs; without
  it, the PINs cannot be checked. Home Assistant's backups keep both
- `secret_key` - the session signing key, generated on the first start
- `logs/log.txt` - audit log: the last 180 days, at most 5 MB, the oldest
  entries making room for new ones

## Upgrading

- **3.1**: the stored PINs are keyed with a new `pin_key`, created on the first
  start; no PIN changes. An admin password on the keypad port must now be at
  least 12 characters, or DoorOpener does not start.

- **3.0**: one door became a list of **Doors**. Home Assistant does not carry
  the old door entity over, so add your door again on the configuration page
  (make it the first one) and restart. Existing users get that first door;
  doors you add later need ticking per user. One-time PINs became guests with
  1 use. The rotated log files are folded into the one circular log.

- **2.10**: DoorOpener's own settings moved into the **DoorOpener settings**
  section. Home Assistant does not carry values across such a move, so check
  them once after updating.
