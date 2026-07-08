import pytest
import json
import os
import tempfile
from unittest.mock import patch
from datetime import datetime, timezone

from users_store import UsersStore, UsersStoreError, _now_iso


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

    with pytest.raises(KeyError, match="User already exists"):
        users_store.create_user("testuser", "5678")

    users = users_store.list_users()["users"]
    assert len(users) == 1


def test_create_duplicate_pin(users_store):
    """Test that creating a user with an in-use PIN fails."""
    users_store.create_user("alice", "1234")

    with pytest.raises(ValueError, match="PIN already in use"):
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

    with pytest.raises(ValueError, match="PIN already in use"):
        users_store.update_user("bob", pin="1234")


def test_update_user_keeps_own_pin(users_store):
    """Test that re-setting a user's own PIN is not flagged as a duplicate."""
    users_store.create_user("alice", "1234")

    users_store.update_user("alice", pin="1234", active=False)

    assert users_store.lookup_pin("1234") is None  # inactive -> not in active map


def test_update_nonexistent_user(users_store):
    """Test updating a user that doesn't exist."""
    with pytest.raises(KeyError, match="User not found"):
        users_store.update_user("nonexistent", pin="1234")


def test_delete_user(users_store):
    """Test deleting a user."""
    users_store.create_user("testuser", "1234")

    users_store.delete_user("testuser")

    users = users_store.list_users()["users"]
    assert len(users) == 0


def test_delete_nonexistent_user(users_store):
    """Test deleting a user that doesn't exist."""
    with pytest.raises(KeyError, match="User not found"):
        users_store.delete_user("nonexistent")


def test_touch_user_new_user(users_store):
    """Test touching a user increments times_used and updates last_used_at."""
    users_store.create_user("testuser", "1234")

    users_store.touch_user("testuser")

    users = users_store.list_users()["users"]
    user = users[0]
    assert user["times_used"] == 1
    assert user["last_used_at"] is not None


def test_touch_user_multiple_times(users_store):
    """Test touching a user multiple times increments counter."""
    users_store.create_user("testuser", "1234")

    users_store.touch_user("testuser")
    users_store.touch_user("testuser")
    users_store.touch_user("testuser")

    users = users_store.list_users()["users"]
    user = users[0]
    assert user["times_used"] == 3


def test_touch_user_backward_compatibility(users_store):
    """Test that touch_user works with existing users without times_used field."""
    # Manually create a user without times_used field (simulating old data)
    users_store._ensure_loaded()
    users_store.data["users"]["olduser"] = {
        "pin": "1234",
        "active": True,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "last_used_at": None,
    }
    users_store._save_atomic()

    users_store.touch_user("olduser")

    users = users_store.list_users()["users"]
    user = next(u for u in users if u["username"] == "olduser")
    assert user["times_used"] == 1  # Should default to 0 and increment to 1


def test_touch_nonexistent_user(users_store):
    """Test touching a user that doesn't exist."""
    # Should not raise an exception
    users_store.touch_user("nonexistent")

    users = users_store.list_users()["users"]
    assert len(users) == 0


def test_user_exists(users_store):
    """Test user_exists method."""
    assert not users_store.user_exists("testuser")

    users_store.create_user("testuser", "1234")
    assert users_store.user_exists("testuser")

    users_store.delete_user("testuser")
    assert not users_store.user_exists("testuser")


def test_list_users_with_pins(users_store):
    """Test listing users includes times_used for JSON users."""
    users_store.create_user("jsonuser", "1234")
    users_store.touch_user("jsonuser")

    result = users_store.list_users()

    assert len(result["users"]) == 1
    user = result["users"][0]
    assert user["username"] == "jsonuser"
    assert user["times_used"] == 1


def test_list_users_without_pins(users_store):
    """Test listing users returns only JSON store users."""
    users_store.create_user("jsonuser", "1234")

    result = users_store.list_users(include_pins=False)

    assert len(result["users"]) == 1
    assert result["users"][0]["username"] == "jsonuser"


def test_file_persistence(temp_users_file):
    """Test that data persists across UsersStore instances."""
    # Create user with first instance
    store1 = UsersStore(temp_users_file)
    store1.create_user("testuser", "1234")
    store1.touch_user("testuser")

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
    users_store.touch_user("user1")

    # Verify the structure matches expected schema
    with open(users_store.path, "r") as f:
        data = json.load(f)

    assert "users" in data
    assert isinstance(data["users"], dict)

    for username, user_data in data["users"].items():
        assert isinstance(username, str)
        assert "pin" in user_data
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
        store.lookup_pin("1234")
    with pytest.raises(UsersStoreError):
        store.create_user("bob", "5678")

    # The on-disk file must be byte-for-byte untouched.
    with open(temp_users_file, "r", encoding="utf-8") as f:
        assert f.read() == corrupt

    # And it must be retryable once the file is fixed (not latched as loaded).
    assert store._loaded is False
    with open(temp_users_file, "w", encoding="utf-8") as f:
        json.dump({"users": {"alice": {"pin": "1234", "active": True}}}, f)
    assert store.lookup_pin("1234") == "alice"


def test_wrong_shape_file_raises(temp_users_file):
    """A structurally valid JSON with the wrong shape must also fail loud."""
    with open(temp_users_file, "w", encoding="utf-8") as f:
        json.dump({"users": ["not", "a", "dict"]}, f)

    store = UsersStore(temp_users_file)
    with pytest.raises(UsersStoreError):
        store.lookup_pin("1234")


def test_empty_file_treated_as_empty_store(temp_users_file):
    """A zero-byte/whitespace file has nothing to lose and loads as an empty store."""
    with open(temp_users_file, "w", encoding="utf-8") as f:
        f.write("   \n")

    store = UsersStore(temp_users_file)
    assert store.lookup_pin("1234") is None
    store.create_user("alice", "1234")
    assert store.lookup_pin("1234") == "alice"


def test_save_cross_device_path_writes_correctly(temp_users_file):
    """When os.replace can't rename (cross-device), the copy path still writes
    correct content and leaves no leftover .bak file behind."""
    store = UsersStore(temp_users_file)
    store.create_user("alice", "1234")

    with patch("users_store.os.replace", side_effect=OSError("cross-device")):
        store.update_user("alice", pin="5678")

    with open(temp_users_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["users"]["alice"]["pin"] == "5678"
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
