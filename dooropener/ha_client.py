"""Home Assistant API client.

DoorOpener needs Home Assistant for one thing, opening the door. Everything else
here (alerts, the opening events, the battery level) is a best-effort extra: it
is skipped when no Home Assistant is configured and never raises, so an absent
or unreachable Home Assistant cannot break the keypad or the admin panel.
"""

import logging
import time

import requests
from requests.adapters import HTTPAdapter

import config

logger = logging.getLogger("dooropener")

# The service that opens the door, per entity domain. Anything else is a switch.
_OPEN_SERVICES = {
    "lock": "lock/unlock",
    "input_boolean": "input_boolean/turn_on",
    "button": "button/press",
}

# Fired on every opening, so Home Assistant automations can react to who came in.
DOOR_OPENED_EVENT = "dooropener_door_opened"


class HAError(Exception):
    """A Home Assistant call failed. The message is safe to log: it never holds the URL."""


class HAClient:
    """Thin wrapper around the HA REST API.

    Configuration values (``entity_id``, ``ha_url``, etc.) are read from
    the ``config`` module at call time so that tests can monkeypatch them.
    """

    __slots__ = ("session", "_battery_cache", "_battery_cache_ts")

    _BATTERY_TTL = 60  # seconds

    def __init__(self):
        self.session = requests.Session()
        adapter = HTTPAdapter(pool_connections=1, pool_maxsize=4)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.verify = config.ha_ca_bundle if config.ha_ca_bundle else True
        self.session.headers.update(
            {
                "Authorization": f"Bearer {config.ha_token}",
                "Content-Type": "application/json",
            }
        )
        self._battery_cache: int | None = None
        self._battery_cache_ts: float = -(self._BATTERY_TTL + 1)

    @property
    def available(self) -> bool:
        """True when a Home Assistant is configured (an app always has one)."""
        return bool(config.ha_token)

    def open_door(self) -> None:
        """Call the service that opens ``config.entity_id``. Raises :class:`HAError`."""
        if not self.available:
            raise HAError("No Home Assistant configured")
        eid = config.entity_id
        self._call_service(_OPEN_SERVICES.get(eid.split(".", 1)[0], "switch/turn_on"), {"entity_id": eid})

    def notify(self, title: str, message: str) -> None:
        """Best effort: show a persistent notification in Home Assistant."""
        self._best_effort(
            "notification",
            self._call_service,
            "persistent_notification/create",
            # A fixed id replaces the previous alert instead of stacking a new one.
            {"title": title, "message": message, "notification_id": "dooropener_alert"},
        )

    def door_opened(self, user: str) -> None:
        """Best effort: tell Home Assistant who opened the door, as an event and in the logbook."""
        self._best_effort("event", self._post, f"/api/events/{DOOR_OPENED_EVENT}",
                          {"user": user, "entity_id": config.entity_id})
        self._best_effort("logbook entry", self._call_service, "logbook/log",
                          {"name": "DoorOpener", "message": f"opened by {user}", "entity_id": config.entity_id})

    def check_connection(self) -> None:
        """Log once whether Home Assistant answers, so a wrong URL or token shows up at startup."""
        if not self.available:
            logger.warning("No Home Assistant configured: the keypad works, but cannot open the door.")
            return
        try:
            resp = self.session.get(f"{config.ha_url}/api/", timeout=10)
        except requests.RequestException as exc:
            logger.warning("Home Assistant unreachable (%s)", type(exc).__name__)
            return
        if resp.status_code == 200:
            logger.info("Connected to Home Assistant")
        elif resp.status_code == 401:
            logger.warning("Home Assistant refused the access token")
        else:
            logger.warning("Home Assistant API error: %s", resp.status_code)

    def get_battery_level(self) -> int | None:
        """Fetch battery percentage (0-100) or ``None``. Cached for 60s."""
        if not config.battery_entity or not self.available:
            return None
        now = time.monotonic()
        if now - self._battery_cache_ts < self._BATTERY_TTL:
            return self._battery_cache
        try:
            resp = self.session.get(
                f"{config.ha_url}/api/states/{config.battery_entity}",
                timeout=10,
            )
            if resp.status_code != 200:
                logger.error("Battery fetch HTTP %s", resp.status_code)
                return self._battery_cache  # return stale on error
            state = resp.json().get("state")
            if state is not None:
                level = float(state)
                if 0 <= level <= 100:
                    self._battery_cache = int(level)
                    self._battery_cache_ts = now
                    return self._battery_cache
        except Exception as exc:
            logger.warning("Battery fetch error: %s", exc)
        return self._battery_cache

    def _best_effort(self, what: str, call, *args) -> None:
        if not self.available:
            return
        try:
            call(*args)
        except HAError as exc:
            logger.warning("Could not send Home Assistant %s: %s", what, exc)

    def _call_service(self, service: str, data: dict) -> None:
        self._post(f"/api/services/{service}", data)

    def _post(self, path: str, data: dict) -> None:
        try:
            resp = self.session.post(f"{config.ha_url}{path}", json=data, timeout=10)
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise HAError(f"Home Assistant API error: {getattr(exc.response, 'status_code', '?')}") from exc
        except requests.RequestException as exc:
            # The exception text can contain the Home Assistant URL; keep only its kind.
            raise HAError(f"Home Assistant unreachable ({type(exc).__name__})") from exc
