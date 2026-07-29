import hmac
import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional


class UsersStoreError(RuntimeError):
    """Raised when the on-disk users store cannot be safely read or written."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class UsersStore:
    """JSON-backed user store with cached reverse PIN lookup.

    - JSON schema:
      {
        "users": {
          "alice": {"pin": "1234", "active": true, "created_at": "...", "updated_at": "...", "last_used_at": null}
        }
      }
    - Maintains a cached ``{pin: username}`` reverse map, invalidated on any CRUD operation.
    """

    __slots__ = (
        "path", "data", "_loaded", "_pin_cache",
        "_touch_dirty", "_last_touch_flush",
    )

    # Coalesce rapid usage writes: apply in memory instantly, persist at most
    # once per this interval (plus an atexit flush) instead of fsync-per-open.
    _TOUCH_FLUSH_INTERVAL = 30.0  # seconds

    def __init__(self, path: str):
        self.path = path
        self.data: Dict[str, Any] = {"users": {}}
        self._loaded = False
        self._pin_cache: Dict[str, str] | None = None  # pin -> username
        self._touch_dirty = False  # unpersisted touch(es) in self.data
        self._last_touch_flush = 0.0  # monotonic ts of last touch flush

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
            # users" would let the very next login or admin edit permanently
            # overwrite the real data with an empty store. Fail loudly instead
            # and leave the on-disk file untouched.
            raise UsersStoreError(f"Cannot parse users store at {self.path}: {e}") from e
        if not isinstance(data, dict) or not isinstance(data.get("users"), dict):
            raise UsersStoreError(f"Users store at {self.path} has an unexpected format")
        self._set_loaded(data)

    def _set_loaded(self, data: Dict[str, Any]) -> None:
        """Commit *data* as the loaded state and invalidate the PIN cache.

        Only reached on a successful load; a raised ``UsersStoreError`` leaves
        ``_loaded`` False so a later call can retry once the file is fixed.
        """
        self.data = data
        self._loaded = True
        self._pin_cache = None  # invalidate on load

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
        self._pin_cache = None  # invalidate on write

    def get_pin_map(self) -> Dict[str, str]:
        """Return cached ``{pin: username}`` dict of active users."""
        self._ensure_loaded()
        if self._pin_cache is not None:
            return self._pin_cache
        result: Dict[str, str] = {}
        for user, meta in self.data.get("users", {}).items():
            if not bool(meta.get("active", True)):
                continue
            pin = meta.get("pin")
            if isinstance(pin, str) and 4 <= len(pin) <= 8 and pin.isdigit():
                result[pin] = user
        self._pin_cache = result
        return result

    def lookup_pin(self, pin: str) -> str | None:
        """Return username for *pin*, or ``None`` if no match."""
        return self.get_pin_map().get(pin)

    def find_disabled_user_by_pin(self, pin: str) -> str | None:
        """Return username if *pin* belongs to a disabled account, else ``None``."""
        self._ensure_loaded()
        for user, meta in self.data.get("users", {}).items():
            stored_pin = meta.get("pin", "")
            if not (isinstance(stored_pin, str) and 4 <= len(stored_pin) <= 8 and stored_pin.isdigit()):
                continue
            if not bool(meta.get("active", True)) and hmac.compare_digest(stored_pin, pin):
                return user
        return None

    def list_users(self, include_pins: bool = False) -> Dict[str, Any]:
        self._ensure_loaded()
        items = []
        for user, meta in self.data.get("users", {}).items():
            item = {
                "username": user,
                "active": bool(meta.get("active", True)),
                "created_at": meta.get("created_at"),
                "updated_at": meta.get("updated_at"),
                "last_used_at": meta.get("last_used_at"),
                "times_used": meta.get("times_used", 0),
            }
            if include_pins:
                item["pin"] = meta.get("pin")
            items.append(item)
        return {"users": items}

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._load_file()

    @staticmethod
    def _validate_username(username: str) -> bool:
        if not isinstance(username, str) or not (1 <= len(username) <= 32):
            return False
        allowed = set(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."
        )
        return all(c in allowed for c in username)

    @staticmethod
    def _validate_pin(pin: str) -> bool:
        return isinstance(pin, str) and pin.isdigit() and 4 <= len(pin) <= 8

    def _pin_in_use(self, pin: str, exclude: Optional[str] = None) -> bool:
        """Return ``True`` if *pin* is already assigned to another user.

        Checks all users (active or not) to prevent collisions in the
        ``{pin: username}`` reverse map. *exclude* skips the user being updated.
        """
        for user, meta in self.data.get("users", {}).items():
            if user == exclude:
                continue
            stored_pin = meta.get("pin", "")
            if isinstance(stored_pin, str) and hmac.compare_digest(stored_pin, pin):
                return True
        return False

    def create_user(self, username: str, pin: str, active: bool = True) -> None:
        self._ensure_loaded()
        if not self._validate_username(username):
            raise ValueError("Invalid username")
        if not self._validate_pin(pin):
            raise ValueError("Invalid pin")
        if username in self.data["users"]:
            raise KeyError("User already exists")
        if self._pin_in_use(pin):
            raise ValueError("PIN already in use")
        now = _now_iso()
        self.data["users"][username] = {
            "pin": pin,
            "active": bool(active),
            "created_at": now,
            "updated_at": now,
            "last_used_at": None,
            "times_used": 0,
        }
        self._save_atomic()

    def update_user(
        self, username: str, pin: Optional[str] = None, active: Optional[bool] = None
    ) -> None:
        self._ensure_loaded()
        if username not in self.data["users"]:
            raise KeyError("User not found")
        if pin is not None and not self._validate_pin(pin):
            raise ValueError("Invalid pin")
        if pin is not None and self._pin_in_use(pin, exclude=username):
            raise ValueError("PIN already in use")
        if active is not None:
            active = bool(active)
        meta = self.data["users"][username]
        if pin is not None:
            meta["pin"] = pin
        if active is not None:
            meta["active"] = active
        meta["updated_at"] = _now_iso()
        self._save_atomic()

    def delete_user(self, username: str) -> None:
        self._ensure_loaded()
        if username not in self.data["users"]:
            raise KeyError("User not found")
        del self.data["users"][username]
        self._save_atomic()

    def touch_user(self, username: str) -> None:
        """Record a usage event.

        The counter/last-used timestamp are updated in memory immediately (so
        reads are always current), but disk writes are coalesced: at most one
        atomic write per ``_TOUCH_FLUSH_INTERVAL``. Call :meth:`flush_touches`
        (e.g. from an ``atexit`` handler) to persist a trailing burst.
        """
        self._ensure_loaded()
        meta = self.data["users"].get(username)
        if meta is None:
            return
        meta["last_used_at"] = _now_iso()
        meta["times_used"] = meta.get("times_used", 0) + 1
        self._touch_dirty = True
        if time.monotonic() - self._last_touch_flush >= self._TOUCH_FLUSH_INTERVAL:
            self.flush_touches()

    def flush_touches(self) -> None:
        """Persist any pending in-memory touch events with a single atomic write."""
        if not self._touch_dirty:
            return
        self._touch_dirty = False
        self._last_touch_flush = time.monotonic()
        self._save_atomic()

    def user_exists(self, username: str) -> bool:
        self._ensure_loaded()
        return username in self.data["users"]
