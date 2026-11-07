"""Users and their PINs, kept in one JSON file::

    {
      "pin_hashing": {"scheme": "scrypt-16384-8-1", "salt": "<hex>"},
      "users": {
        "alice": {"pin_hash": "<hex>", "active": true, "doors": ["barrier", "door"],
                  "access": null, "phone_key": "<hex>",
                  "visits": 0, "visit_started_at": null, "finished": null, "finished_at": null,
                  "created_at": "...", "updated_at": "...", "last_used_at": null, "times_used": 0}
      }
    }

PINs are stored hashed (see ``pins``) and can never be read back. The hashes are
keyed with the PIN key, a file kept apart from users.json (``key_file``): without
it, users.json cannot be searched for PINs. A store written before 2.8.0 keeps
PINs in plain text under "pin", and one from before 3.1 unkeyed hashes; both are
converted the first time they are loaded, as are the access rules of a store
from before 3.0. Losing the PIN key loses every PIN, so it is backed up with
users.json; a store that needs it and cannot find it refuses to load.

``doors`` are the ids of the doors a user may open. ``access`` holds the rules of
``access`` (``null``: always); ``visits`` and ``visit_started_at`` count a guest's
uses. A guest whose end date passes, or whose uses run out, is switched off
(``active`` false) with the reason in ``finished``, and stays in the list.
``phone_key`` lets the keypad recognise this user's phones; it changes with the PIN.
"""

import json
import logging
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
from datetime import UTC, datetime
from typing import Any

import access
from pins import KEYED_SCHEME, MIN_PIN_KEY_LENGTH, PIN_HASH_SCHEME, hash_pin, is_valid_pin, key_pin_hash, pin_key_id

logger = logging.getLogger("dooropener")

# Why a PIN that matches nobody, or a disabled user, was refused (audit statuses).
# A guest switched off by their end date or uses keeps that reason instead.
NO_MATCH = "AUTH_FAILURE"
DISABLED = "DISABLED"

# update_user(access_rules=KEEP, doors=KEEP) leaves those as they are; None clears the rules.
KEEP = object()

_DOOR_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,23}")


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


NO_VISIT = access.NO_USAGE


def _usage(meta: dict) -> access.Usage:
    """A user's visits so far, as ``access`` reads them."""
    try:
        start = datetime.fromisoformat(meta["visit_started_at"]) if meta.get("visit_started_at") else None
    except (TypeError, ValueError):
        start = None
    if start is not None and start.tzinfo is None:
        start = None  # every stored visit start has an offset; anything else is not one
    return access.Usage(visits=int(meta.get("visits") or 0), visit_start=start)


class UsersStore:
    """The users file, with a cached ``{pin hash: username}`` index for lookups."""

    __slots__ = (
        "path", "key_file", "data", "_loaded", "_index", "_key",
        "_touch_dirty", "_last_touch_flush", "_lock",
    )

    # Coalesce rapid usage writes: apply in memory instantly, persist at most
    # once per this interval (plus an atexit flush) instead of fsync-per-open.
    _TOUCH_FLUSH_INTERVAL = 30.0  # seconds

    def __init__(self, path: str, key_file: str | None = None):
        self.path = path
        # The PIN key: read from here, or created here the first time. None keeps
        # the hashes unkeyed (tests, and stores that are only inspected).
        self.key_file = key_file
        self.data: dict[str, Any] = {"users": {}}
        self._loaded = False
        self._index: dict[str, str] | None = None  # pin hash -> username
        self._key: bytes | None = None
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
            self._key = self._pin_key(None)
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
            self._key = self._pin_key(None)
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
        if hashing is not None and (not isinstance(hashing, dict)
                                    or hashing.get("scheme") not in (PIN_HASH_SCHEME, KEYED_SCHEME)
                                    or not isinstance(hashing.get("salt"), str)):
            raise UsersStoreError(f"Users store at {self.path} uses an unknown PIN hashing scheme")
        self._key = self._pin_key(hashing)
        self._set_loaded(data)

    def _pin_key(self, hashing: dict | None) -> bytes | None:
        """The PIN key for a store hashed as *hashing* says. Raises UsersStoreError
        rather than guess: a new key for keyed PINs would silently void them all."""
        keyed = bool(hashing) and hashing.get("scheme") == KEYED_SCHEME
        if not self.key_file:
            if keyed:
                raise UsersStoreError(f"The PINs in {self.path} need a PIN key, and none is configured")
            return None
        try:
            with open(self.key_file, encoding="utf-8") as f:
                text = f.read().strip()
        except FileNotFoundError:
            if keyed:
                raise UsersStoreError(
                    f"The PIN key {self.key_file} is missing, and the PINs in {self.path} cannot be checked "
                    "without it. Restore it from the backup that holds users.json; nothing was changed."
                ) from None
            text = self._create_pin_key()
        except OSError as e:
            raise UsersStoreError(f"Cannot read the PIN key {self.key_file}: {e}") from e
        if len(text) < MIN_PIN_KEY_LENGTH:
            raise UsersStoreError(f"The PIN key in {self.key_file} is shorter than {MIN_PIN_KEY_LENGTH} characters")
        key = text.encode()
        if keyed and hashing.get("key_id") != pin_key_id(key):
            raise UsersStoreError(
                f"The PIN key {self.key_file} is not the one the PINs in {self.path} were made with; "
                "nothing was changed."
            )
        return key

    def _create_pin_key(self) -> str:
        key_dir = os.path.dirname(self.key_file)
        if key_dir:
            os.makedirs(key_dir, exist_ok=True)
        text = secrets.token_hex(32)
        try:
            fd = os.open(self.key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError as e:
            raise UsersStoreError(f"Cannot create the PIN key {self.key_file}: {e}") from e
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text + "\n")
            f.flush()
            os.fsync(f.fileno())
        logger.info("Created the PIN key %s: back it up with users.json", self.key_file)
        return text

    def _set_loaded(self, data: dict[str, Any]) -> None:
        """Commit *data* as the loaded state, converting plain-text PINs.

        Only reached on a successful load; a raised ``UsersStoreError`` leaves
        ``_loaded`` False so a later call can retry once the file is fixed.
        """
        self.data = data
        self._loaded = True
        self._index = None
        keyed = self._key_stored_hashes()
        hashed = self._hash_plain_pins()
        upgraded = self._upgrade_users()
        if keyed or hashed or upgraded:
            try:
                self._save_atomic()
                logger.info("Converted the users store to this version's format")
            except (OSError, UsersStoreError) as e:
                # The converted store works from memory; the next write saves it.
                logger.warning("Could not save the converted users store: %s", e)

    def _key_stored_hashes(self) -> bool:
        """Key the unkeyed hashes of a store from before 3.1 with the PIN key. True if it did."""
        hashing = self.data.get("pin_hashing")
        if self._key is None or not hashing or hashing.get("scheme") != PIN_HASH_SCHEME:
            return False
        for meta in self.data["users"].values():
            if isinstance(meta, dict) and isinstance(meta.get("pin_hash"), str):
                meta["pin_hash"] = key_pin_hash(meta["pin_hash"], self._key)
        hashing["scheme"], hashing["key_id"] = KEYED_SCHEME, pin_key_id(self._key)
        logger.info("Keyed the stored PIN hashes with the PIN key")
        return True

    def _upgrade_users(self) -> bool:
        """Bring users written by an older version up to date. True if any changed."""
        changed = False
        for meta in self.data["users"].values():
            if not isinstance(meta, dict):
                continue
            rules = access.upgrade(meta.get("access"))
            if rules != meta.get("access"):
                meta["access"] = rules
                changed = True
            if not meta.get("phone_key"):
                meta["phone_key"] = secrets.token_hex(8)
                changed = True
        return changed

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
                if self._key is not None:
                    hashing["scheme"], hashing["key_id"] = KEYED_SCHEME, pin_key_id(self._key)
            salt = bytes.fromhex(hashing["salt"])
            key = self._key if hashing["scheme"] == KEYED_SCHEME else None
        digest = hash_pin(pin, salt)  # slow on purpose, so outside the lock
        return key_pin_hash(digest, key) if key is not None else digest

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
        outside its access rules. *now* is the door's local time (aware). Which
        doors the user may open is the caller's to check (see :meth:`profile`).
        """
        with self._lock:
            self._ensure_loaded()
        user = self._owner(self._digest(pin))
        if user is None:
            return None, NO_MATCH
        with self._lock:
            meta = self.data["users"].get(user) or {}
            if not meta.get("active", True):
                return user, meta.get("finished") or DISABLED
            return user, access.denial(meta.get("access"), now, _usage(meta))

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
                    "doors": list(meta.get("doors") or []),
                    "access": meta.get("access"),
                    "visits": meta.get("visits", 0),
                    "finished": meta.get("finished"),
                    "finished_at": meta.get("finished_at"),
                    "created_at": meta.get("created_at"),
                    "updated_at": meta.get("updated_at"),
                    "last_used_at": meta.get("last_used_at"),
                    "times_used": meta.get("times_used", 0),
                })
            return {"users": items}

    def profile(self, username: str) -> dict[str, Any]:
        """What the keypad needs about a user: doors, rules, phone key, and why they were switched off."""
        with self._lock:
            self._ensure_loaded()
            meta = self.data["users"].get(username) or {}
            return {
                "doors": list(meta.get("doors") or []),
                "access": meta.get("access"),
                "phone_key": meta.get("phone_key") or "",
                "finished": meta.get("finished"),
                "finished_at": meta.get("finished_at"),
            }

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

    @staticmethod
    def _doors(raw: Any) -> list[str]:
        """Door ids, in order, each once. Whether the doors exist is the caller's to check."""
        if raw is None:
            return []
        if not isinstance(raw, list) or not all(isinstance(d, str) and _DOOR_ID.fullmatch(d) for d in raw):
            raise InvalidUser("doors must be a list of door ids")
        return list(dict.fromkeys(raw))

    def create_user(
        self, username: str, pin: str, active: bool = True, access_rules: Any = None, doors: Any = None,
    ) -> None:
        if not self._validate_username(username):
            raise InvalidUser("Invalid username")
        if not is_valid_pin(pin):
            raise InvalidUser("PIN must be 4-8 digits")
        rules = self._rules(access_rules)
        door_ids = self._doors(doors)
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
                "doors": door_ids,
                "access": rules,
                "phone_key": secrets.token_hex(8),
                "visits": 0,
                "visit_started_at": None,
                "finished": None,
                "finished_at": None,
                "created_at": now,
                "updated_at": now,
                "last_used_at": None,
                "times_used": 0,
            }
            self._save_atomic()

    def update_user(
        self, username: str, pin: str | None = None, active: bool | None = None, access_rules: Any = KEEP,
        doors: Any = KEEP, now: datetime | None = None,
    ) -> None:
        """Change a user. Switching a guest back on needs an end date still ahead of *now*
        (the door's local time), and gives them their uses afresh."""
        if pin is not None and not is_valid_pin(pin):
            raise InvalidUser("PIN must be 4-8 digits")
        rules = KEEP if access_rules is KEEP else self._rules(access_rules)
        door_ids = KEEP if doors is KEEP else self._doors(doors)
        now = now or datetime.now(UTC).astimezone()
        with self._lock:
            self._ensure_loaded()
            if username not in self.data["users"]:
                raise UserNotFound("User not found")
            meta = self.data["users"][username]
            new_rules = meta.get("access") if rules is KEEP else rules
            switching_on = bool(active) and not meta.get("active", True)
            if switching_on and access.finished(new_rules, NO_VISIT, now) == access.EXPIRED:
                raise InvalidUser("This guest's end date has passed: set a new one to switch them back on")
            if pin is not None:
                digest = self._digest(pin)
                if self._owner(digest) not in (None, username):
                    raise UserConflict("PIN already in use")
                meta["pin_hash"] = digest
                meta["phone_key"] = secrets.token_hex(8)  # phones that knew the old PIN are forgotten
            if active is not None:
                meta["active"] = bool(active)
            if switching_on:
                meta["finished"] = meta["finished_at"] = None
                meta["visits"], meta["visit_started_at"] = 0, None
            if rules is not KEEP:
                meta["access"] = rules
            if door_ids is not KEEP:
                meta["doors"] = door_ids
            meta["updated_at"] = _now_iso()
            self._save_atomic()

    def delete_user(self, username: str) -> None:
        with self._lock:
            self._ensure_loaded()
            if username not in self.data["users"]:
                raise UserNotFound("User not found")
            del self.data["users"][username]
            self._save_atomic()

    def adopt_doors(self, door_ids: list[str]) -> int:
        """Give *door_ids* to every user who has no door list yet (a store from before 3.0).

        Returns how many users got them. Users who already have a list keep it, so a
        door added later is opened by nobody until the admin ticks it for them.
        """
        with self._lock:
            self._ensure_loaded()
            missing = [meta for meta in self.data["users"].values() if isinstance(meta, dict) and "doors" not in meta]
            for meta in missing:
                meta["doors"] = list(door_ids)
            if missing:
                self._save_atomic()
            return len(missing)

    def retire(self, now: datetime) -> list[tuple[str, str]]:
        """Switch off the guests whose end date has passed or whose uses ran out.

        They stay in the list, disabled, with the reason in ``finished``. Returns
        ``(username, reason)`` for each one switched off now. *now* is aware local time.
        """
        with self._lock:
            self._ensure_loaded()
            done = []
            for user, meta in self.data["users"].items():
                if not isinstance(meta, dict) or not meta.get("active", True):
                    continue
                reason = access.finished(meta.get("access"), _usage(meta), now)
                if reason:
                    meta["active"] = False
                    meta["finished"], meta["finished_at"] = reason, _now_iso()
                    meta["updated_at"] = meta["finished_at"]
                    done.append((user, reason))
            if done:
                self._touch_dirty = False  # the write below carries any pending counts too
                self._save_atomic()
            return done

    def record_use(self, username: str, now: datetime | None = None) -> str | None:
        """Count an opening. For a guest with a number of uses, the first opening of a
        visit counts one: returns "use 1 of 3" when it does, else None.

        Counts are kept in memory at once but written at most every
        ``_TOUCH_FLUSH_INTERVAL``; :meth:`flush_touches` (run at exit) writes the
        rest. A counted use is written immediately.
        """
        now = now or datetime.now(UTC)
        with self._lock:
            self._ensure_loaded()
            meta = self.data["users"].get(username)
            if meta is None:
                return None
            meta["last_used_at"] = _now_iso()
            meta["times_used"] = meta.get("times_used", 0) + 1
            self._touch_dirty = True
            note = None
            if not access.in_visit(_usage(meta), now):
                meta["visits"] = meta.get("visits", 0) + 1
                meta["visit_started_at"] = now.isoformat()
                uses = (meta.get("access") or {}).get("uses")
                if uses:
                    note = f"use {meta['visits']} of {uses}"
                    self.flush_touches()
                    return note
            if time.monotonic() - self._last_touch_flush >= self._TOUCH_FLUSH_INTERVAL:
                self.flush_touches()
            return note

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
