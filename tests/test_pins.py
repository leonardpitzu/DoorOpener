"""PIN rules: validation, comparison, hashing and the generator."""
import pytest

import pins
from pins import PIN_HASH_SCHEME, generate_pin, hash_pin, is_valid_pin, secure_equals, trivial_pins


@pytest.mark.parametrize("pin", ["1234", "00000000", "482915"])
def test_valid_pins(pin):
    assert is_valid_pin(pin)


@pytest.mark.parametrize("pin", [
    "123",                    # too short
    "123456789",              # too long
    "12a4",                   # not a digit
    "١٢٣٤",  # Arabic-Indic digits: isdigit() says yes
    "¹²³⁴",  # superscripts: isdigit() says yes
    1234,                     # not a string
    None,
])
def test_invalid_pins(pin):
    assert not is_valid_pin(pin)


def test_secure_equals_accepts_any_text():
    # hmac.compare_digest raises TypeError on non-ASCII str; this must not.
    assert secure_equals("türschloß", "türschloß")
    assert not secure_equals("١٢٣٤", "1234")


def test_trivial_pins():
    four = trivial_pins(4)
    assert {"0000", "9999", "0123", "6789", "9876", "3210"} <= four
    assert "7890" not in four  # wraps around, not a straight run
    assert len(four) == 10 + 7 + 7
    assert len(trivial_pins(8)) == 10 + 3 + 3


@pytest.mark.parametrize("length", range(4, 9))
def test_generated_pin_is_valid_unused_and_not_trivial(length):
    taken = {"482915", "1357"}
    for _ in range(200):
        pin = generate_pin(length, taken.__contains__)
        assert is_valid_pin(pin) and len(pin) == length
        assert pin not in taken
        assert pin not in trivial_pins(length)


def test_generator_draws_again_past_trivial_and_taken_pins(monkeypatch):
    draws = iter([1234, 4821, 5093])  # trivial, taken, free
    monkeypatch.setattr(pins.secrets, "randbelow", lambda _n: next(draws))
    assert generate_pin(4, {"4821"}.__contains__) == "5093"


def test_generator_gives_up_when_every_draw_is_taken():
    assert generate_pin(4, lambda _pin: True, tries=50) is None


@pytest.mark.parametrize("length", [3, 9])
def test_generator_rejects_bad_lengths(length):
    with pytest.raises(ValueError):
        generate_pin(length, lambda _pin: False)


def test_hashes_depend_on_pin_and_salt():
    assert hash_pin("1234", b"salt") == hash_pin("1234", b"salt")
    assert hash_pin("1234", b"salt") != hash_pin("1235", b"salt")
    assert hash_pin("1234", b"salt") != hash_pin("1234", b"pepper")


def test_the_hash_costs_what_its_scheme_name_says(monkeypatch):
    # conftest makes hashing cheap for speed; the real parameters are checked here.
    # Changing them without renaming the scheme would orphan every stored PIN.
    monkeypatch.undo()
    cost = pins._SCRYPT
    assert cost["n"] == 2**14
    assert PIN_HASH_SCHEME == f"scrypt-{cost['n']}-{cost['r']}-{cost['p']}"
