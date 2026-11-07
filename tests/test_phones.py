"""Recognising a user's phone, and the pass behind the door tiles."""
from passes import Passes
from phones import MAX_USERS, KnownPhones


def test_a_phone_is_known_for_a_user_and_key():
    phones = KnownPhones("a-secret-key-0123456789")
    cookie = phones.remember(None, "kid", "k1")
    assert phones.knows(cookie, "kid", "k1")
    assert not phones.knows(cookie, "kid", "k2")       # the PIN changed since
    assert not phones.knows(cookie, "dad", "k1")
    assert not phones.knows(None, "kid", "k1")
    assert not phones.knows(cookie, "kid", "")


def test_a_cookie_signed_with_another_key_is_ignored():
    forged = KnownPhones("someone-elses-key-0123").remember(None, "kid", "k1")
    assert KnownPhones("a-secret-key-0123456789").users(forged) == {}
    assert KnownPhones("a-secret-key-0123456789").users("not a cookie") == {}


def test_a_shared_phone_remembers_a_few_people():
    phones = KnownPhones("a-secret-key-0123456789")
    cookie = None
    for n in range(MAX_USERS + 2):
        cookie = phones.remember(cookie, f"user{n}", "k")
    known = phones.users(cookie)
    assert len(known) == MAX_USERS and f"user{MAX_USERS + 1}" in known and "user0" not in known
    cookie = phones.remember(cookie, "user2", "new")  # an update, not a duplicate
    assert phones.users(cookie)["user2"] == "new" and len(phones.users(cookie)) == MAX_USERS


def test_a_new_pin_entry_replaces_the_browsers_earlier_pass():
    passes = Passes()
    first = passes.issue("leo", ["barrier", "door"], "s1")
    second = passes.issue("leo", ["barrier", "door"], "s1")
    assert passes.redeem(first, "door", "s1") is None
    assert passes.redeem(second, "door", "s1") == "leo"
