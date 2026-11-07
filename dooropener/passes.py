"""The short pass between a PIN and the door the user then picks.

A PIN that may open several doors does not open any yet: the keypad shows them
as tiles, and the server keeps a pass for that user and those doors. Tapping a
tile spends it. The pass lives only here, in memory (the app runs one worker),
so it works once, for one browser, for ``TTL``, and a copy of a cookie or of a
response cannot replay it.
"""

import secrets
import threading
import time
from dataclasses import dataclass

TTL = 30  # seconds


@dataclass(frozen=True)
class _Pass:
    user: str
    doors: tuple[str, ...]
    session_id: str
    expires: float


class Passes:
    def __init__(self, ttl: float = TTL, clock=time.monotonic):
        self.ttl = ttl
        self._clock = clock
        self._passes: dict[str, _Pass] = {}
        self._lock = threading.Lock()

    def issue(self, user: str, doors: list[str], session_id: str) -> str:
        """A new pass for *user* to open one of *doors* from this browser. Returns its token."""
        token = secrets.token_urlsafe(24)
        with self._lock:
            now = self._clock()
            self._passes = {t: p for t, p in self._passes.items() if p.expires > now}
            # One pass per browser: entering the PIN again replaces the previous one.
            self._passes = {t: p for t, p in self._passes.items() if p.session_id != session_id}
            self._passes[token] = _Pass(user, tuple(doors), session_id, now + self.ttl)
        return token

    def redeem(self, token: str, door: str, session_id: str) -> str | None:
        """The user the pass was for, if it is valid for *door* from this browser. It is spent either way."""
        with self._lock:
            found = self._passes.pop(token, None)
        if found is None or found.expires <= self._clock() or found.session_id != session_id:
            return None
        return found.user if door in found.doors else None
