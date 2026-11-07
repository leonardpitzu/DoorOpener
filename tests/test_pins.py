"""PIN rules: validation, comparison and the generator."""
import pytest

from pins import generate_pin, is_valid_pin, secure_equals, trivial_pins


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
        pin = generate_pin(length, taken)
        assert is_valid_pin(pin) and len(pin) == length
        assert pin not in taken
        assert pin not in trivial_pins(length)


def test_generator_finds_the_last_free_pin():
    free = "4821"
    taken = {f"{n:04d}" for n in range(10_000)} - {free}
    assert generate_pin(4, taken) == free


def test_generator_reports_an_exhausted_space():
    taken = {f"{n:04d}" for n in range(10_000)}
    assert generate_pin(4, taken) is None


def test_generator_ignores_pins_of_other_lengths_and_junk():
    taken = ["12345", "abcd", "١٢٣٤", 1234]
    assert len(generate_pin(4, taken)) == 4


def test_generator_spreads_over_the_free_space():
    # Only 3 non-trivial PINs are free; each should come up.
    keep = {"1000", "5050", "9001"}
    taken = {f"{n:04d}" for n in range(10_000)} - keep
    assert {generate_pin(4, taken) for _ in range(300)} == keep


@pytest.mark.parametrize("length", [3, 9])
def test_generator_rejects_bad_lengths(length):
    with pytest.raises(ValueError):
        generate_pin(length, set())
