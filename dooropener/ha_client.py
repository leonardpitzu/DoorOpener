"""Home Assistant API client."""

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
        adapter = HTTPAdapter(pool_connections=1, pool_maxsize=2)
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

    def open_door(self) -> None:
        """Call the service that opens ``config.entity_id``. Raises :class:`HAError`."""
        eid = config.entity_id
        self._call_service(_OPEN_SERVICES.get(eid.split(".", 1)[0], "switch/turn_on"), {"entity_id": eid})

    def notify(self, title: str, message: str) -> None:
        """Best effort: show a persistent notification in Home Assistant. Never raises."""
        try:
            self._call_service(
                "persistent_notification/create",
                # A fixed id replaces the previous alert instead of stacking a new one.
                {"title": title, "message": message, "notification_id": "dooropener_alert"},
            )
        except HAError as exc:
            logger.warning("Could not send Home Assistant notification: %s", exc)

    def _call_service(self, service: str, data: dict) -> None:
        try:
            resp = self.session.post(f"{config.ha_url}/api/services/{service}", json=data, timeout=10)
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise HAError(f"Home Assistant API error: {getattr(exc.response, 'status_code', '?')}") from exc
        except requests.RequestException as exc:
            # The exception text can contain the Home Assistant URL; keep only its kind.
            raise HAError(f"Home Assistant unreachable ({type(exc).__name__})") from exc

    def get_battery_level(self) -> int | None:
        """Fetch battery percentage (0-100) or ``None``. Cached for 60s."""
        if not config.battery_entity:
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
