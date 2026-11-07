"""Brute-force protection, client identity, and security headers."""

import ipaddress
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from flask import request, session
from werkzeug.middleware.proxy_fix import ProxyFix

from config import (
    BLOCK_TIME,
    MAX_ATTEMPTS,
    MAX_GLOBAL_ATTEMPTS_PER_HOUR,
    SESSION_MAX_ATTEMPTS,
    get_current_time,
)

# The longest a block can grow to. Also how long a client is remembered once it
# has gone quiet (no failures, no block): forgetting it sooner would reset the backoff.
MAX_BLOCK_TIME = timedelta(hours=24)
_PRUNE_EVERY = timedelta(hours=1)


# ---------------------------------------------------------------------------
# Throttling
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class _Record:
    last_failure: datetime
    penalty: timedelta  # length of this client's next block
    failures: int = 0
    blocked_until: datetime | None = None

    @property
    def quiet_since(self) -> datetime:
        """When this client last failed or, if later, when its last block ended."""
        return max(self.last_failure, self.blocked_until or self.last_failure)


class Throttle:
    """Blocks a client after *limit* failures, for twice as long each time it happens again.

    Blocks last *block_time*, then 2x, 4x, ... up to ``MAX_BLOCK_TIME``, so waiting a
    block out buys an attacker fewer and fewer guesses per day. A success forgets the
    client; so does a full day with neither failures nor a block.
    """

    def __init__(self, limit: int, block_time: timedelta = BLOCK_TIME):
        self.limit = limit
        self.block_time = block_time
        self._records: dict[str, _Record] = {}
        self._lock = threading.Lock()
        self._next_prune = get_current_time() + _PRUNE_EVERY

    def blocked_until(self, key: str) -> datetime | None:
        """When the active block on *key* ends, or ``None`` if it is not blocked."""
        with self._lock:
            record = self._records.get(key)
            until = record.blocked_until if record else None
        return until if until and until > get_current_time() else None

    def fail(self, key: str) -> datetime | None:
        """Count a failure for *key*. Returns when the block it triggers ends, if it triggers one."""
        now = get_current_time()
        with self._lock:
            self._prune(now)
            record = self._records.get(key)
            if record is None:
                record = self._records[key] = _Record(last_failure=now, penalty=self.block_time)
            record.last_failure = now
            record.failures += 1
            if record.failures < self.limit:
                return None
            record.failures = 0
            record.blocked_until = now + record.penalty
            record.penalty = min(record.penalty * 2, MAX_BLOCK_TIME)
            return record.blocked_until

    def reset(self, key: str) -> None:
        """Forget *key*: its failures, its block, and its backoff."""
        with self._lock:
            self._records.pop(key, None)

    def _prune(self, now: datetime) -> None:
        # Drops only clients that have been quiet for a whole MAX_BLOCK_TIME, so
        # nothing blocked is dropped, and a client that just sat out the longest
        # block keeps its backoff.
        if now < self._next_prune:
            return
        self._next_prune = now + _PRUNE_EVERY
        stale = now - MAX_BLOCK_TIME
        self._records = {k: r for k, r in self._records.items() if r.quiet_since > stale}


class FailureAlarm:
    """Counts failures from every client and goes off once an hour when they reach *threshold*.

    It never blocks anyone. The throttles key on client IPs, so a global lockout would
    let a single attacker deny the door to everyone with a handful of junk guesses.
    """

    def __init__(self, threshold: int):
        self.threshold = threshold
        self._window_start = get_current_time()
        self._count = 0
        self._lock = threading.Lock()

    def record(self) -> bool:
        """Count one failure. True exactly when it is the one that reaches the threshold."""
        now = get_current_time()
        with self._lock:
            if now - self._window_start >= timedelta(hours=1):
                self._window_start, self._count = now, 0
            self._count += 1
            return self._count == self.threshold


class RateLimiter:
    """All brute-force state: keypad PINs by IP and by session, admin logins by IP."""

    def __init__(self):
        self.pin_ip = Throttle(MAX_ATTEMPTS)
        self.pin_session = Throttle(SESSION_MAX_ATTEMPTS)
        self.admin_ip = Throttle(SESSION_MAX_ATTEMPTS)
        self.alarm = FailureAlarm(MAX_GLOBAL_ATTEMPTS_PER_HOUR)

    def pin_blocked_until(self, ip: str, session_id: str) -> datetime | None:
        return _latest(self.pin_ip.blocked_until(ip), self.pin_session.blocked_until(session_id))

    def pin_failed(self, ip: str, session_id: str) -> datetime | None:
        """Count a wrong PIN. Returns when the block it triggers ends, if it triggers one."""
        return _latest(self.pin_ip.fail(ip), self.pin_session.fail(session_id))

    def pin_succeeded(self, ip: str, session_id: str) -> None:
        self.pin_ip.reset(ip)
        self.pin_session.reset(session_id)


def _latest(*times: datetime | None) -> datetime | None:
    return max((t for t in times if t), default=None)


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------
def client_identity() -> tuple[str, str]:
    """The client's IP and session id: the only keys the throttles use.

    Nothing else a client sends (User-Agent, Accept-Language, ...) may go into a
    throttle key, since varying it would hand an attacker a fresh counter. The IP is
    only as trustworthy as the ``trusted_proxies`` setting.
    """
    session_id = session.get("_session_id")
    if not session_id:
        session_id = session["_session_id"] = secrets.token_hex(16)
    return request.remote_addr or "unknown", session_id


class LocalProxyFix:
    """Believe X-Forwarded-* only from a reverse proxy on the local network.

    Behind a proxy, X-Forwarded-For names the visitor, but anyone can send the
    header. It counts only when the connection itself comes from a private,
    loopback or link-local address, where a proxy of yours runs (NGINX Proxy
    Manager, a Cloudflare tunnel, Caddy, ...). A client on the internet that
    reaches the port around the proxy keeps its own address, so the brute-force
    limits cannot be dodged with a made-up one.
    """

    def __init__(self, app, hops: int):
        self.app = app
        self.proxied = ProxyFix(app, x_for=hops, x_proto=hops, x_host=hops)

    def __call__(self, environ, start_response):
        handler = self.proxied if _is_local(environ.get("REMOTE_ADDR")) else self.app
        return handler(environ, start_response)


def _is_local(address: str | None) -> bool:
    try:
        ip = ipaddress.ip_address(address or "")
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local


def add_security_headers(response, framable: bool = False):
    """Attach hardening headers to every response.

    Scripts load only from DoorOpener itself, never inline. *framable* lets the
    same origin embed the page, which Home Assistant does with ingress pages.
    """
    frame_ancestors = "'self'" if framable else "'none'"
    response.headers.update({
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN" if framable else "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": (
            "geolocation=(), microphone=(), camera=(), payment=(), usb=(), "
            "magnetometer=(), gyroscope=(), fullscreen=(self), "
            "browsing-topics=(), run-ad-auction=(), join-ad-interest-group=(), "
            "private-aggregation=(), attribution-reporting=(), compute-pressure=()"
        ),
        "X-Permitted-Cross-Domain-Policies": "none",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Content-Security-Policy": (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            "connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; "
            f"frame-ancestors {frame_ancestors}"
        ),
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
    })
    return response
