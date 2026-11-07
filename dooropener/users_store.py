"""Users and their PINs, kept in one JSON file::

    {
      "pin_hashing": {"scheme": "scrypt-16384-8-1", "salt": "<hex>"},
      "users": {
        "alice": {"pin_hash": "<hex>", "active": true, "access": null,
                  "created_at": "...", "updated_at": "...", "last_used_at": null, "times_used": 0}
      }
    }

PINs are stored hashed (see ``pins``) and can never be read back. A store written
before 2.8.0 keeps them in plain text under "pin"; it is converted the first time
it is loaded. ``access`` holds the rules of ``access``; ``null`` means always.
"""

import json
import logging
import os
import secrets
import shutil
import tempfile
import threading
import time
from datetime import UTC, datetime
from typing import Any

import access
from pins import PIN_HASH_SCHEME, hash_pin, is_valid_pin

logger = logging.getLogger("dooropener")

# Why a PIN that matches nobody, or a disabled user, was refused (audit statuses).
NO_MATCH = "AUTH_FAILURE"
DISABLED = "DISABLED"

# update_user(access_rules=KEEP) leaves a user's rules as they are; None clears them.
KEEP = object()


class UsersStoreError(RuntimeError):
    """Raised when the on-disk users store cannot be safely read or written."""


class InvalidUser(ValueError):
    """A username, PIN or access rule that fails validation."""


class UserConflict(ValueError):
    """The username or the PIN already belongs to someone."""


class UserNotFound(KeyError):
    """No user by that name."""

    def __str__(self) -> str:  # KeyError would quote the message
        return self.args[0] if self.args else "User not found"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class UsersStore:
    """The users file, with a cached ``{pin hash: username}`` index for lookups."""

    __slots__ = (
        "path", "data", "_loaded", "_index",
        "_touch_dirty", "_last_touch_flush", "_lock",
    )

    # Coalesce rapid usage writes: apply in memory instantly, persist at most
    # once per this interval (plus an atexit flush) instead of fsync-per-open.
    _TOUCH_FLUSH_INTERVAL = 30.0  # seconds

    def __init__(self, path: str):
        self.path = path
        self.data: dict[str, Any] = {"users": {}}
        self._loaded = False
        self._index: dict[str, str] | None = None  # pin hash -> username
        self._touch_dirty = False  # unpersisted usage counts in self.data
        self._last_touch_flush = 0.0  # monotonic ts of last touch flush
        # Reentrant: gunicorn runs threaded and mutators nest
        # (record_use -> flush_touches -> _save_atomic).
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Loading and saving
    # ------------------------------------------------------------------
    def _load_file(self) -> None:
        if self._loaded:
            return
        if not os.path.exists(self.path):
            dir_name = os.path.dirname(self.path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            self._set_loaded({"users": {}})
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            raise UsersStoreError(f"Cannot read users store at {self.path}: {e}") from e
        if content.strip() == "":
            # A freshly-created/empty file (e.g. touch'd but never written) has
            # no data to lose, so it's safe to treat like a missing file.
            self._set_loaded({"users": {}})
            return
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            # Do NOT fall back to {"users": {}} here. Every mutation loads then
            # immediately re-saves self.data, so treating a corrupt file as "no
            # users" would let the very next admin edit permanently overwrite the
            # real data with an empty store. Fail loudly and leave the file alone.
            raise UsersStoreError(f"Cannot parse users store at {self.path}: {e}") from e
        if not isinstance(data, dict) or not isinstance(data.get("users"), dict):
            raise UsersStoreError(f"Users store at {self.path} has an unexpected format")
        hashing = data.get("pin_hashing")
        if hashing is not None and (not isinstance(hashing, dict) or hashing.get("scheme") != PIN_HASH_SCHEME
                                    or not isinstance(hashing.get("salt"), str)):
            raise UsersStoreError(f"Users store at {self.path} uses an unknown PIN hashing scheme")
        self._set_loaded(data)

    def _set_loaded(self, data: dict[str, Any]) -> None:
        """Commit *data* as the loaded state, converting plain-text PINs.

        Only reached on a successful load; a raised ``UsersStoreError`` leaves
        ``_loaded`` False so a later call can retry once the file is fixed.
        """
        self.data = data
        self._loaded = True
        self._index = None
        if self._hash_plain_pins():
            try:
                self._save_atomic()
                logger.info("Converted the stored PINs to hashes")
            except (OSError, UsersStoreError) as e:
                # The converted store works from memory; the next write saves it.
                logger.warning("Could not save the converted users store: %s", e)

    def _hash_plain_pins(self) -> bool:
        """Hash the plain-text PINs an older version left. True if there were any."""
        plain = [meta for meta in self.data["users"].values() if isinstance(meta, dict) and "pin" in meta]
        for meta in plain:
            pin = meta.pop("pin")
            if is_valid_pin(pin):
                meta["pin_hash"] = self._digest(pin)
        return bool(plain)

    def _save_atomic(self) -> None:
        dir_name = os.path.dirname(self.path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        # Prefer writing the temp file next to the target (same filesystem =
        # atomic rename). Fall back to the system temp dir when the app
        # directory can't take a new file, e.g. a single-file Docker bind-mount,
        # or the primary filesystem is out of space/inodes.
        tmp = None
        for tmp_dir in (dir_name or ".", tempfile.gettempdir()):
            try:
                fd, tmp = tempfile.mkstemp(suffix=".json", prefix=".users-", dir=tmp_dir)
                break
            except OSError:
                continue
        else:
            raise UsersStoreError(
                f"Cannot create temp file in {dir_name or '.'} or {tempfile.gettempdir()}"
            )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            try:
                # Same filesystem: atomic rename, no window where the file is
                # missing or half-written.
                os.replace(tmp, self.path)
            except OSError:
                # Cross-device: os.replace() can't rename across filesystems, so
                # back up the existing file before overwriting it. If the copy
                # below fails partway (disk fills up, process killed), restore
                # from the backup instead of leaving users.json truncated.
                backup_path = self.path + ".bak"
                has_existing = os.path.exists(self.path)
                if has_existing:
                    shutil.copy2(self.path, backup_path)
                try:
                    with open(tmp, "r", encoding="utf-8") as src:
                        content = src.read()
                    with open(self.path, "w", encoding="utf-8") as dst:
                        dst.write(content)
                        dst.flush()
                        os.fsync(dst.fileno())
                except Exception:
                    if has_existing:
                        shutil.copy2(backup_path, self.path)
                    raise
                finally:
                    if has_existing:
                        try:
                            os.remove(backup_path)
                        except OSError:
                            pass
                os.remove(tmp)
        except BaseException:
            try:
                if tmp and os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            raise
        self._index = None  # invalidate on write

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._load_file()

    # ------------------------------------------------------------------
    # PINs
    # ------------------------------------------------------------------
    def _digest(self, pin: str) -> str:
        """The stored form of *pin*. The salt is created with the first PIN."""
        with self._lock:
            hashing = self.data.get("pin_hashing")
            if not hashing:
                hashing = self.data["pin_hashing"] = {"scheme": PIN_HASH_SCHEME, "salt": secrets.token_hex(16)}
            salt = bytes.fromhex(hashing["salt"])
        return hash_pin(pin, salt)  # slow on purpose, so outside the lock

    def _owner(self, digest: str) -> str | None:
        with self._lock:
            if self._index is None:
                self._index = {
                    meta["pin_hash"]: user
                    for user, meta in self.data["users"].items()
                    if isinstance(meta, dict) and isinstance(meta.get("pin_hash"), str)
                }
            return self._index.get(digest)

    def authorize(self, pin: str, now: datetime) -> tuple[str | None, str | None]:
        """Whose PIN this is, and why it may not open the door at *now*.

        Returns ``(username, None)`` for a PIN that works, ``(None, NO_MATCH)`` for
        one nobody has, and ``(username, reason)`` for one that is disabled or
        outside its access rules. *now* is the door's local time.
        """
        with self._lock:
            self._ensure_loaded()
        user = self._owner(self._digest(pin))
        if user is None:
            return None, NO_MATCH
        with self._lock:
            meta = self.data["users"].get(user) or {}
            if not meta.get("active", True):
                return user, DISABLED
            return user, access.denial(meta.get("access"), now)

    def pin_taken(self, pin: str, exclude: str | None = None) -> bool:
        """True if *pin* belongs to a user other than *exclude*, active or not."""
        with self._lock:
            self._ensure_loaded()
        owner = self._owner(self._digest(pin))
        return owner is not None and owner != exclude

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------
    def list_users(self) -> dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            items = []
            for user, meta in self.data["users"].items():
                items.append({
                    "username": user,
                    "active": bool(meta.get("active", True)),
                    "access": meta.get("access"),
                    "created_at": meta.get("created_at"),
                    "updated_at": meta.get("updated_at"),
                    "last_used_at": meta.get("last_used_at"),
                    "times_used": meta.get("times_used", 0),
                })
            return {"users": items}

    @staticmethod
    def _validate_username(username: str) -> bool:
        if not isinstance(username, str) or not (1 <= len(username) <= 32):
            return False
        allowed = set(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."
        )
        return all(c in allowed for c in username)

    @staticmethod
    def _rules(raw: Any) -> dict | None:
        try:
            return access.normalize(raw)
        except ValueError as e:
            raise InvalidUser(str(e)) from e

    def create_user(self, username: str, pin: str, active: bool = True, access_rules: Any = None) -> None:
        if not self._validate_username(username):
            raise InvalidUser("Invalid username")
        if not is_valid_pin(pin):
            raise InvalidUser("PIN must be 4-8 digits")
        rules = self._rules(access_rules)
        with self._lock:
            self._ensure_loaded()
            if username in self.data["users"]:
                raise UserConflict("User already exists")
            digest = self._digest(pin)
            if self._owner(digest) is not None:
                raise UserConflict("PIN already in use")
            now = _now_iso()
            self.data["users"][username] = {
                "pin_hash": digest,
                "active": bool(active),
                "access": rules,
                "created_at": now,
                "updated_at": now,
                "last_used_at": None,
                "times_used": 0,
            }
            self._save_atomic()

    def update_user(
        self, username: str, pin: str | None = None, active: bool | None = None, access_rules: Any = KEEP,
    ) -> None:
        if pin is not None and not is_valid_pin(pin):
            raise InvalidUser("PIN must be 4-8 digits")
        rules = KEEP if access_rules is KEEP else self._rules(access_rules)
        with self._lock:
            self._ensure_loaded()
            if username not in self.data["users"]:
                raise UserNotFound("User not found")
            meta = self.data["users"][username]
            if pin is not None:
                digest = self._digest(pin)
                if self._owner(digest) not in (None, username):
                    raise UserConflict("PIN already in use")
                meta["pin_hash"] = digest
            if active is not None:
                meta["active"] = bool(active)
            if rules is not KEEP:
                meta["access"] = rules
            meta["updated_at"] = _now_iso()
            self._save_atomic()

    def delete_user(self, username: str) -> None:
        with self._lock:
            self._ensure_loaded()
            if username not in self.data["users"]:
                raise UserNotFound("User not found")
            del self.data["users"][username]
            self._save_atomic()

    def record_use(self, username: str) -> bool:
        """Count an opening. A one-time PIN is deactivated by it: returns True when that happened.

        The count is kept in memory at once but written at most every
        ``_TOUCH_FLUSH_INTERVAL``; :meth:`flush_touches` (run at exit) writes the
        rest. Deactivating a one-time PIN is written immediately.
        """
        with self._lock:
            self._ensure_loaded()
            meta = self.data["users"].get(username)
            if meta is None:
                return False
            meta["last_used_at"] = _now_iso()
            meta["times_used"] = meta.get("times_used", 0) + 1
            self._touch_dirty = True
            if access.is_one_time(meta.get("access")):
                meta["active"] = False
                meta["updated_at"] = meta["last_used_at"]
                self.flush_touches()
                return True
            if time.monotonic() - self._last_touch_flush >= self._TOUCH_FLUSH_INTERVAL:
                self.flush_touches()
            return False

    def flush_touches(self) -> None:
        """Persist any pending in-memory usage counts with a single atomic write."""
        with self._lock:
            if not self._touch_dirty:
                return
            self._touch_dirty = False
            self._last_touch_flush = time.monotonic()
            self._save_atomic()

    def user_exists(self, username: str) -> bool:
        with self._lock:
            self._ensure_loaded()
            return username in self.data["users"]
