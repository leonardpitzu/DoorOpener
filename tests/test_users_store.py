import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from users_store import (
    DISABLED, KEEP, NO_MATCH, InvalidUser, UserConflict, UserNotFound, UsersStore, UsersStoreError, _now_iso,
)

NOW = datetime(2026, 11, 11, 12, 0)  # a Wednesday, noon at the door


def _who(store, pin, now=NOW):
    """The user a PIN opens the door for, or None."""
    user, refusal = store.authorize(pin, now)
    return user if refusal is None else None


@pytest.fixture
def temp_users_file():
    """Create a temporary users.json file for testing."""
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def users_store(temp_users_file):
    """Create a UsersStore instance with a temporary file."""
    return UsersStore(temp_users_file)


def test_users_store_initialization(users_store):
    """Test that UsersStore initializes correctly."""
    assert users_store.path
    assert users_store.data == {"users": {}}
    assert not users_store._loaded


def test_create_user(users_store):
    """Test creating a new user."""
    users_store.create_user("testuser", "1234")

    users = users_store.list_users()["users"]
    assert len(users) == 1

    user = users[0]
    assert user["username"] == "testuser"
    assert user["active"] is True
    assert user["times_used"] == 0
    assert "created_at" in user
    assert "updated_at" in user


def test_create_duplicate_user(users_store):
    """Test that creating a duplicate user fails."""
    users_store.create_user("testuser", "1234")

    with pytest.raises(UserConflict, match="User already exists"):
        users_store.create_user("testuser", "5678")

    users = users_store.list_users()["users"]
    assert len(users) == 1


def test_create_duplicate_pin(users_store):
    """Test that creating a user with an in-use PIN fails."""
    users_store.create_user("alice", "1234")

    with pytest.raises(UserConflict, match="PIN already in use"):
        users_store.create_user("bob", "1234")

    users = users_store.list_users()["users"]
    assert len(users) == 1


def test_update_user(users_store):
    """Test updating an existing user."""
    users_store.create_user("testuser", "1234")

    users_store.update_user("testuser", pin="5678", active=False)

    users = users_store.list_users()["users"]
    user = users[0]
    assert user["active"] is False


def test_update_duplicate_pin(users_store):
    """Test that updating a user to an in-use PIN fails."""
    users_store.create_user("alice", "1234")
    users_store.create_user("bob", "5678")

    with pytest.raises(UserConflict, match="PIN already in use"):
        users_store.update_user("bob", pin="1234")


def test_update_user_keeps_own_pin(users_store):
    """Test that re-setting a user's own PIN is not flagged as a duplicate."""
    users_store.create_user("alice", "1234")

    users_store.update_user("alice", pin="1234", active=False)

    assert users_store.authorize("1234", NOW) == ("alice", DISABLED)


def test_update_nonexistent_user(users_store):
    """Test updating a user that doesn't exist."""
    with pytest.raises(UserNotFound, match="User not found"):
        users_store.update_user("nonexistent", pin="1234")


def test_delete_user(users_store):
    """Test deleting a user."""
    users_store.create_user("testuser", "1234")

    users_store.delete_user("testuser")

    users = users_store.list_users()["users"]
    assert len(users) == 0


def test_delete_nonexistent_user(users_store):
    """Test deleting a user that doesn't exist."""
    with pytest.raises(UserNotFound, match="User not found"):
        users_store.delete_user("nonexistent")


def test_touch_user_new_user(users_store):
    """Test touching a user increments times_used and updates last_used_at."""
    users_store.create_user("testuser", "1234")

    users_store.record_use("testuser")

    users = users_store.list_users()["users"]
    user = users[0]
    assert user["times_used"] == 1
    assert user["last_used_at"] is not None


def test_touch_user_multiple_times(users_store):
    """Test touching a user multiple times increments counter."""
    users_store.create_user("testuser", "1234")

    users_store.record_use("testuser")
    users_store.record_use("testuser")
    users_store.record_use("testuser")

    users = users_store.list_users()["users"]
    user = users[0]
    assert user["times_used"] == 3


def test_touch_user_backward_compatibility(users_store):
    """Test that touch_user works with existing users without times_used field."""
    # Manually create a user without times_used field (simulating old data)
    users_store._ensure_loaded()
    users_store.data["users"]["olduser"] = {
        "pin_hash": "00",
        "active": True,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "last_used_at": None,
    }
    users_store._save_atomic()

    users_store.record_use("olduser")

    users = users_store.list_users()["users"]
    user = next(u for u in users if u["username"] == "olduser")
    assert user["times_used"] == 1  # Should default to 0 and increment to 1


def test_touch_nonexistent_user(users_store):
    """Test touching a user that doesn't exist."""
    # Should not raise an exception
    users_store.record_use("nonexistent")

    users = users_store.list_users()["users"]
    assert len(users) == 0


def test_user_exists(users_store):
    """Test user_exists method."""
    assert not users_store.user_exists("testuser")

    users_store.create_user("testuser", "1234")
    assert users_store.user_exists("testuser")

    users_store.delete_user("testuser")
    assert not users_store.user_exists("testuser")


def test_list_users_counts_uses(users_store):
    """Listing users includes times_used."""
    users_store.create_user("jsonuser", "1234")
    users_store.record_use("jsonuser")

    result = users_store.list_users()

    assert len(result["users"]) == 1
    user = result["users"][0]
    assert user["username"] == "jsonuser"
    assert user["times_used"] == 1


def test_list_users_never_includes_pins(users_store):
    users_store.create_user("jsonuser", "1234")

    (user,) = users_store.list_users()["users"]

    assert user["username"] == "jsonuser"
    assert not {"pin", "pin_hash"} & set(user)


def test_file_persistence(temp_users_file):
    """Test that data persists across UsersStore instances."""
    # Create user with first instance
    store1 = UsersStore(temp_users_file)
    store1.create_user("testuser", "1234")
    store1.record_use("testuser")

    # Load with second instance
    store2 = UsersStore(temp_users_file)
    users = store2.list_users()["users"]

    assert len(users) == 1
    user = users[0]
    assert user["username"] == "testuser"
    assert user["times_used"] == 1


def test_atomic_save_error_handling(users_store):
    """Test that atomic save handles errors gracefully."""
    users_store.create_user("testuser", "1234")

    # Mock tempfile.NamedTemporaryFile to raise an exception
    with patch("tempfile.NamedTemporaryFile", side_effect=OSError("Mock error")):
        # Should not raise an exception, but should handle it gracefully
        try:
            users_store.create_user("testuser2", "5678")
        except Exception:
            pytest.fail("_save_atomic should handle errors gracefully")


def test_now_iso_format():
    """Test that _now_iso returns properly formatted ISO string."""
    iso_string = _now_iso()

    # Should be parseable as datetime
    parsed = datetime.fromisoformat(
        iso_string.replace("Z", "+00:00") if iso_string.endswith("Z") else iso_string
    )
    assert isinstance(parsed, datetime)

    # Should be recent (within last minute)
    now = datetime.now(timezone.utc)
    diff = abs((now - parsed).total_seconds())
    assert diff < 60


def test_json_schema_validation(users_store):
    """Test that the JSON schema is maintained correctly."""
    users_store.create_user("user1", "1234")
    users_store.create_user("user2", "5678")
    users_store.record_use("user1")

    # Verify the structure matches expected schema
    with open(users_store.path, "r") as f:
        data = json.load(f)

    assert "users" in data
    assert isinstance(data["users"], dict)

    for username, user_data in data["users"].items():
        assert isinstance(username, str)
        assert "pin_hash" in user_data and "pin" not in user_data
        assert "active" in user_data
        assert "created_at" in user_data
        assert "updated_at" in user_data
        assert "last_used_at" in user_data
        assert "times_used" in user_data
        assert isinstance(user_data["times_used"], int)
        assert user_data["times_used"] >= 0


def test_corrupt_file_raises_and_preserves_data(temp_users_file):
    """A corrupt users.json must fail loud and NOT be overwritten with an empty store.

    Regression test for the silent-data-loss bug: previously any load error fell
    back to {"users": {}}, so the next mutation wiped the real file.
    """
    corrupt = '{"users": {"alice": {"pin": "1234"' + " broken"
    with open(temp_users_file, "w", encoding="utf-8") as f:
        f.write(corrupt)

    store = UsersStore(temp_users_file)

    # Every entry point that loads must raise instead of silently emptying.
    with pytest.raises(UsersStoreError):
        store.authorize("1234", NOW)
    with pytest.raises(UsersStoreError):
        store.create_user("bob", "5678")

    # The on-disk file must be byte-for-byte untouched.
    with open(temp_users_file, "r", encoding="utf-8") as f:
        assert f.read() == corrupt

    # And it must be retryable once the file is fixed (not latched as loaded).
    assert store._loaded is False
    with open(temp_users_file, "w", encoding="utf-8") as f:
        json.dump({"users": {"alice": {"pin": "1234", "active": True}}}, f)
    assert _who(store, "1234") == "alice"


def test_wrong_shape_file_raises(temp_users_file):
    """A structurally valid JSON with the wrong shape must also fail loud."""
    with open(temp_users_file, "w", encoding="utf-8") as f:
        json.dump({"users": ["not", "a", "dict"]}, f)

    store = UsersStore(temp_users_file)
    with pytest.raises(UsersStoreError):
        store.authorize("1234", NOW)


def test_empty_file_treated_as_empty_store(temp_users_file):
    """A zero-byte/whitespace file has nothing to lose and loads as an empty store."""
    with open(temp_users_file, "w", encoding="utf-8") as f:
        f.write("   \n")

    store = UsersStore(temp_users_file)
    assert store.authorize("1234", NOW) == (None, NO_MATCH)
    store.create_user("alice", "1234")
    assert _who(store, "1234") == "alice"


def test_save_cross_device_path_writes_correctly(temp_users_file):
    """When os.replace can't rename (cross-device), the copy path still writes
    correct content and leaves no leftover .bak file behind."""
    store = UsersStore(temp_users_file)
    store.create_user("alice", "1234")

    with patch("users_store.os.replace", side_effect=OSError("cross-device")):
        store.update_user("alice", pin="5678")

    assert _who(UsersStore(temp_users_file), "5678") == "alice"
    assert not os.path.exists(temp_users_file + ".bak")


def test_save_recovers_from_cross_device_failure(temp_users_file):
    """If the cross-device copy fails mid-write, the original file is restored
    from the backup instead of being left truncated."""
    store = UsersStore(temp_users_file)
    store.create_user("alice", "1234")
    original = open(temp_users_file, "r", encoding="utf-8").read()

    real_open = open
    calls = {"n": 0}

    def failing_dest_open(path, mode="r", *args, **kwargs):
        # Fail only the first write to the live users.json (the direct copy
        # step in _save_atomic); everything else uses the real open.
        if path == temp_users_file and "w" in mode:
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("disk full")
        return real_open(path, mode, *args, **kwargs)

    def restore_copy2(src, dst):
        with real_open(src, "rb") as s, real_open(dst, "wb") as d:
            d.write(s.read())

    with patch("users_store.os.replace", side_effect=OSError("cross-device")):
        with patch("users_store.shutil.copy2", side_effect=restore_copy2):
            with patch("builtins.open", side_effect=failing_dest_open):
                with pytest.raises(OSError):
                    store.update_user("alice", pin="5678")

    # Original content must survive intact and no backup left behind.
    assert open(temp_users_file, "r", encoding="utf-8").read() == original
    assert not os.path.exists(temp_users_file + ".bak")


# ---------------------------------------------------------------------------
# Hashed PINs
# ---------------------------------------------------------------------------
def test_pins_are_never_written_in_plain_text(users_store):
    users_store.create_user("alice", "482915")
    with open(users_store.path, encoding="utf-8") as f:
        assert "482915" not in f.read()
    assert _who(users_store, "482915") == "alice"
    assert _who(users_store, "482916") is None


def test_plain_text_pins_from_older_versions_are_converted(temp_users_file):
    with open(temp_users_file, "w", encoding="utf-8") as f:
        json.dump({"users": {"alice": {"pin": "1234", "active": True},
                             "bob": {"pin": "5678", "active": False},
                             "junk": {"pin": "12ab", "active": True}}}, f)

    store = UsersStore(temp_users_file)
    assert _who(store, "1234") == "alice"
    assert store.authorize("5678", NOW) == ("bob", DISABLED)

    with open(temp_users_file, encoding="utf-8") as f:
        saved = json.load(f)
    assert not any("pin" in meta for meta in saved["users"].values())
    assert "pin_hash" not in saved["users"]["junk"]  # an unusable PIN is dropped
    assert _who(UsersStore(temp_users_file), "1234") == "alice"  # and it all survives a reload


def test_an_unknown_hashing_scheme_is_refused(temp_users_file):
    with open(temp_users_file, "w", encoding="utf-8") as f:
        json.dump({"pin_hashing": {"scheme": "argon2-something", "salt": "00"}, "users": {}}, f)
    with pytest.raises(UsersStoreError, match="hashing scheme"):
        UsersStore(temp_users_file).authorize("1234", NOW)


def test_pin_taken_covers_disabled_users_but_not_the_owner(users_store):
    users_store.create_user("alice", "1234", active=False)
    assert users_store.pin_taken("1234")
    assert not users_store.pin_taken("1234", exclude="alice")
    assert not users_store.pin_taken("4321")


# ---------------------------------------------------------------------------
# Access rules, guests and their uses
# ---------------------------------------------------------------------------
UTC = timezone.utc
FRIDAY = "2026-11-13T23:59"


def test_access_rules_are_stored_and_listed(users_store):
    users_store.create_user("guest", "2468", access_rules={"valid_until": "2026-11-12T18:00"})
    (user,) = users_store.list_users()["users"]
    assert user["access"]["valid_until"] == "2026-11-12T18:00"
    assert users_store.authorize("2468", datetime(2026, 11, 13, 9, 0)) == ("guest", "EXPIRED")


def test_update_keeps_or_clears_access_rules(users_store):
    users_store.create_user("guest", "2468", access_rules={"valid_until": FRIDAY, "uses": 1})
    users_store.update_user("guest", pin="1357")
    assert users_store.list_users()["users"][0]["access"]["uses"] == 1
    users_store.update_user("guest", access_rules=KEEP)
    assert users_store.list_users()["users"][0]["access"] is not None
    users_store.update_user("guest", access_rules=None)
    assert users_store.list_users()["users"][0]["access"] is None


def test_invalid_access_rules_are_refused(users_store):
    with pytest.raises(InvalidUser, match="days"):
        users_store.create_user("guest", "2468", access_rules={"schedule": [{"days": [], "hours": None}]})
    with pytest.raises(InvalidUser, match="end date"):
        users_store.create_user("courier", "8642", access_rules={"uses": 1})
    assert users_store.list_users()["users"] == []


def test_a_courier_gets_one_visit_then_is_switched_off(users_store):
    users_store.create_user("courier", "8642", doors=["barrier", "door"], access_rules={
        "valid_until": "2026-11-09T23:59", "uses": 1, "schedule": [{"days": [0], "hours": ["10:00", "13:30"]}]})
    arrival = datetime(2026, 11, 9, 10, 5, tzinfo=UTC)
    assert users_store.record_use("courier", arrival) == "use 1 of 1"
    # Written at once, not left for the next usage flush.
    assert UsersStore(users_store.path).list_users()["users"][0]["visits"] == 1
    later = arrival + timedelta(minutes=10)
    assert users_store.authorize("8642", later) == ("courier", None)  # the door after the barrier
    assert users_store.record_use("courier", later) is None           # the same visit
    assert users_store.retire(later) == []                            # not mid-visit
    done = arrival + timedelta(minutes=15)
    assert users_store.authorize("8642", done) == ("courier", "USED_UP")
    assert users_store.retire(done) == [("courier", "USED_UP")]
    (user,) = users_store.list_users()["users"]
    assert (user["active"], user["finished"]) == (False, "USED_UP") and user["finished_at"]
    assert users_store.authorize("8642", done) == ("courier", "USED_UP")  # still says why


def test_a_guest_is_switched_off_when_the_end_date_passes(users_store):
    users_store.create_user("friend", "2468", doors=["door"], access_rules={"valid_until": "2026-11-15T23:59"})
    assert users_store.retire(datetime(2026, 11, 15, 23, 0, tzinfo=UTC)) == []
    assert users_store.retire(datetime(2026, 11, 16, 0, 0, tzinfo=UTC)) == [("friend", "EXPIRED")]
    assert users_store.retire(datetime(2026, 11, 16, 0, 1, tzinfo=UTC)) == []  # once
    assert users_store.authorize("2468", datetime(2026, 11, 16, 9, 0)) == ("friend", "EXPIRED")


def test_an_expired_guest_comes_back_only_with_a_new_end_date(users_store):
    users_store.create_user("friend", "2468", access_rules={"valid_until": "2026-11-15T23:59"})
    users_store.retire(datetime(2026, 11, 16, 0, 0, tzinfo=UTC))
    monday = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
    with pytest.raises(InvalidUser, match="end date has passed"):
        users_store.update_user("friend", active=True, now=monday)
    users_store.update_user("friend", active=True, access_rules={"valid_until": "2026-11-22T23:59"}, now=monday)
    (user,) = users_store.list_users()["users"]
    assert (user["active"], user["finished"], user["finished_at"]) == (True, None, None)
    assert users_store.authorize("2468", monday) == ("friend", None)


def test_switching_a_used_up_guest_back_on_gives_their_uses_afresh(users_store):
    users_store.create_user("courier", "8642", access_rules={"valid_until": FRIDAY, "uses": 1})
    start = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
    users_store.record_use("courier", start)
    users_store.retire(start + timedelta(hours=1))
    users_store.update_user("courier", active=True, now=start + timedelta(hours=2))
    assert users_store.list_users()["users"][0]["visits"] == 0
    assert users_store.authorize("8642", start + timedelta(hours=2)) == ("courier", None)


def test_an_ordinary_pin_survives_its_use(users_store):
    users_store.create_user("alice", "1234")
    assert users_store.record_use("alice") is None
    assert _who(users_store, "1234") == "alice"
    assert users_store.retire(datetime(2027, 1, 1, tzinfo=UTC)) == []  # full-time users never finish


# ---------------------------------------------------------------------------
# Doors, and recognising a user's phones
# ---------------------------------------------------------------------------
def test_doors_are_stored_in_order_each_once(users_store):
    users_store.create_user("alice", "1234", doors=["door", "barrier", "door"])
    assert users_store.profile("alice")["doors"] == ["door", "barrier"]
    users_store.update_user("alice", doors=["barrier"])
    assert users_store.list_users()["users"][0]["doors"] == ["barrier"]
    users_store.update_user("alice", pin="4321")  # doors kept
    assert users_store.profile("alice")["doors"] == ["barrier"]
    with pytest.raises(InvalidUser, match="door ids"):
        users_store.update_user("alice", doors=["Front Door"])


def test_users_from_before_3_0_adopt_the_first_door_and_new_doors_need_a_tick(temp_users_file):
    with open(temp_users_file, "w") as f:
        json.dump({"users": {"alice": {"pin": "1234", "active": True,
                                       "access": {"valid_from": None, "valid_until": None, "days": [0],
                                                  "hours": None, "one_time": False}}}}, f)
    store = UsersStore(temp_users_file)
    assert store.adopt_doors(["door"]) == 1
    assert store.adopt_doors(["barrier", "door"]) == 0  # a door added later is nobody's yet
    profile = store.profile("alice")
    assert profile["doors"] == ["door"]
    assert profile["access"] == {"valid_from": None, "valid_until": None, "uses": None,
                                 "schedule": [{"days": [0], "hours": None}]}
    assert len(profile["phone_key"]) == 16
    saved = json.load(open(temp_users_file))["users"]["alice"]
    assert saved["doors"] == ["door"] and "pin" not in saved and saved["phone_key"] == profile["phone_key"]


def test_a_new_pin_forgets_the_phones_that_knew_the_old_one(users_store):
    users_store.create_user("alice", "1234")
    key = users_store.profile("alice")["phone_key"]
    users_store.update_user("alice", doors=["door"])
    assert users_store.profile("alice")["phone_key"] == key
    users_store.update_user("alice", pin="4321")
    assert users_store.profile("alice")["phone_key"] not in ("", key)
