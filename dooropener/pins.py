"""PIN rules: what a valid PIN is, how it is stored, and how to pick a good one."""

import hashlib
import hmac
import secrets
from collections.abc import Callable

PIN_MIN_LENGTH = 4
PIN_MAX_LENGTH = 8
DEFAULT_PIN_LENGTH = 6

_DIGITS = "0123456789"

# Stored PINs are scrypt hashes, salted per store. With 4-8 digits, scrypt alone
# only slows an attacker holding a copy of users.json (about 16 MiB and a few tens
# of ms per guess: a 4-digit PIN falls in minutes), so the hash is also keyed with
# a secret kept apart from users.json, the PIN key (KEYED_SCHEME). Without the key
# a copy of users.json cannot be searched for PINs at all; scrypt still stands
# behind it should the key leak too. The salt is shared so a PIN can be looked up
# with one hash, not one per user.
PIN_HASH_SCHEME = "scrypt-16384-8-1"
KEYED_SCHEME = PIN_HASH_SCHEME + "+hmac-sha256"
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024, "dklen": 32}

MIN_PIN_KEY_LENGTH = 32  # characters; a generated key is 64 hex digits

# The admin password on the keypad port: at least this long unless given as a hash.
MIN_ADMIN_PASSWORD_LENGTH = 12
ADMIN_HASH_PREFIX = "scrypt:"


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


def key_pin_hash(digest: str, key: bytes) -> str:
    """A scrypt PIN hash keyed with the PIN key. Works on stored hashes too, so
    a store is converted without knowing any PIN."""
    return hmac.new(key, bytes.fromhex(digest), hashlib.sha256).hexdigest()


def pin_key_id(key: bytes) -> str:
    """A fingerprint of the PIN key, kept with the PINs, so a wrong key is noticed at once."""
    return hmac.new(key, b"dooropener pin key", hashlib.sha256).hexdigest()[:16]


def hash_password(password: str) -> str:
    """An admin password as scrypt:n:r:p:salt:hash, for admin_password instead of the password."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"{ADMIN_HASH_PREFIX}{_SCRYPT['n']}:{_SCRYPT['r']}:{_SCRYPT['p']}:{salt.hex()}:{digest.hex()}"


def is_password_hash(value: str) -> bool:
    return value.startswith(ADMIN_HASH_PREFIX)


def check_password(password: str, stored: str) -> bool:
    """Whether *password* is the admin password *stored*, as a hash or as plain text."""
    if not is_password_hash(stored):
        return secure_equals(password, stored)
    try:
        n, r, p, salt, digest = stored.removeprefix(ADMIN_HASH_PREFIX).split(":")
        candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                                   maxmem=_SCRYPT["maxmem"], dklen=len(digest) // 2)
    except ValueError:
        return False
    return hmac.compare_digest(candidate.hex(), digest)


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


def _hash_password_command() -> int:
    """python pins.py hash-password: asks for a password and prints its hash."""
    import getpass
    first = getpass.getpass("Admin password: ")
    if len(first) < MIN_ADMIN_PASSWORD_LENGTH:
        print(f"Use at least {MIN_ADMIN_PASSWORD_LENGTH} characters.")
        return 1
    if getpass.getpass("Again: ") != first:
        print("The two did not match.")
        return 1
    print(hash_password(first))
    return 0


if __name__ == "__main__":
    import sys
    if sys.argv[1:] != ["hash-password"]:
        print("Usage: python pins.py hash-password")
        sys.exit(2)
    sys.exit(_hash_password_command())
