"""PIN rules: what a valid PIN is, how it is stored, and how to pick a good one."""

import hashlib
import hmac
import secrets
from collections.abc import Callable

PIN_MIN_LENGTH = 4
PIN_MAX_LENGTH = 8
DEFAULT_PIN_LENGTH = 6

_DIGITS = "0123456789"

# Stored PINs are scrypt hashes, salted per store. With 4-8 digits that slows an
# attacker holding a copy of users.json (about 16 MiB and a few tens of ms per
# guess) rather than stopping them; it does keep the PINs out of plain sight.
# The salt is shared so a PIN can be looked up with one hash, not one per user.
PIN_HASH_SCHEME = "scrypt-16384-8-1"
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024, "dklen": 32}


def is_valid_pin(pin: object) -> bool:
    """4-8 ASCII digits.

    ``str.isdigit()`` alone also accepts digits such as '١' or '²', which no keypad
    can type.
    """
    return (
        isinstance(pin, str)
        and pin.isascii()
        and pin.isdigit()
        and PIN_MIN_LENGTH <= len(pin) <= PIN_MAX_LENGTH
    )


def hash_pin(pin: str, salt: bytes) -> str:
    return hashlib.scrypt(pin.encode(), salt=salt, **_SCRYPT).hex()


def secure_equals(a: str, b: str) -> bool:
    """Constant-time comparison that, unlike ``hmac.compare_digest`` on str, accepts any text."""
    return hmac.compare_digest(a.encode(), b.encode())


def trivial_pins(length: int) -> set[str]:
    """The PINs anyone tries first: one repeated digit (1111) or a straight run (1234, 4321)."""
    runs = {_DIGITS[i:i + length] for i in range(len(_DIGITS) - length + 1)}
    return {d * length for d in _DIGITS} | runs | {run[::-1] for run in runs}


def generate_pin(length: int, taken: Callable[[str], bool], tries: int = 200) -> str | None:
    """A random, non-trivial PIN of *length* digits for which ``taken(pin)`` is false.

    Stored PINs are hashed, so the free ones cannot be listed; candidates are drawn
    and checked instead. With a few users among 10,000+ PINs the first draw nearly
    always fits. Returns ``None`` if *tries* draws all miss.
    """
    if not PIN_MIN_LENGTH <= length <= PIN_MAX_LENGTH:
        raise ValueError(f"PIN length must be {PIN_MIN_LENGTH}-{PIN_MAX_LENGTH}")
    trivial = trivial_pins(length)
    for _ in range(tries):
        pin = f"{secrets.randbelow(10**length):0{length}d}"
        if pin not in trivial and not taken(pin):
            return pin
    return None
