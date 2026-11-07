"""PIN rules: what a valid PIN is, how to compare one, and how to pick a good one."""

import hmac
import secrets
from collections.abc import Iterable

PIN_MIN_LENGTH = 4
PIN_MAX_LENGTH = 8
DEFAULT_PIN_LENGTH = 6

_DIGITS = "0123456789"


def is_valid_pin(pin: object) -> bool:
    """4-8 ASCII digits.

    ``str.isdigit()`` alone also accepts digits such as '١' or '²', which no keypad
    can type and which ``hmac.compare_digest`` refuses to compare.
    """
    return (
        isinstance(pin, str)
        and pin.isascii()
        and pin.isdigit()
        and PIN_MIN_LENGTH <= len(pin) <= PIN_MAX_LENGTH
    )


def secure_equals(a: str, b: str) -> bool:
    """Constant-time comparison that, unlike ``hmac.compare_digest`` on str, accepts any text."""
    return hmac.compare_digest(a.encode(), b.encode())


def trivial_pins(length: int) -> set[str]:
    """The PINs anyone tries first: one repeated digit (1111) or a straight run (1234, 4321)."""
    runs = {_DIGITS[i:i + length] for i in range(len(_DIGITS) - length + 1)}
    return {d * length for d in _DIGITS} | runs | {run[::-1] for run in runs}


def generate_pin(length: int, taken: Iterable[str]) -> str | None:
    """A random PIN of *length* digits that is neither in *taken* nor trivial.

    Draws uniformly from the PINs that are actually free (so it cannot give up early
    on a crowded space) and returns ``None`` only when none is left.
    """
    if not PIN_MIN_LENGTH <= length <= PIN_MAX_LENGTH:
        raise ValueError(f"PIN length must be {PIN_MIN_LENGTH}-{PIN_MAX_LENGTH}")
    excluded = sorted(
        {int(pin) for pin in taken if is_valid_pin(pin) and len(pin) == length}
        | {int(pin) for pin in trivial_pins(length)}
    )
    free = 10**length - len(excluded)
    if free <= 0:
        return None
    # Pick the n-th free number: step past every excluded number at or below it.
    n = secrets.randbelow(free)
    for value in excluded:
        if value > n:
            break
        n += 1
    return f"{n:0{length}d}"
