"""Audit trail: one JSON line per door or admin event, in one circular file.

The log keeps the last ``RETENTION`` (180 days) and at most ``max_bytes``: as new
entries arrive, the oldest are dropped. Before 3.0 the log rotated into
log.txt.1 ... log.txt.5, which the admin panel never showed; those are folded
back into the one file at start, oldest first, and then trimmed like the rest.
"""

import json
import os
import stat
import tempfile
import threading
import time
from datetime import datetime, timedelta

from config import get_current_time

RETENTION = timedelta(days=180)
_TEST_MODE_MARKER = "TEST MODE"
_BYTES_PER_ENTRY = 250  # generous estimate, to read just enough of the file's tail
_PRUNE_EVERY = 3600.0  # seconds: how often old entries are looked for, besides when the file is full
_FILL_AFTER_PRUNE = 0.9  # a full log is trimmed to this share of max_bytes, not rewritten on every entry
_OLD_ROTATIONS = 9


class AuditLog:
    """The event log that the admin dashboard reads back."""

    def __init__(self, path: str, max_bytes: int = 5_000_000, retention: timedelta = RETENTION):
        self.path = path
        self.max_bytes = max_bytes
        self.retention = retention
        self.lock = threading.RLock()
        os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)
        self._size = 0
        self._pruned_at = 0.0
        with self.lock:
            self._fold_old_rotations()
            # The log exists from the start, private: it names people and their addresses.
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600))
            self.prune()

    def record(self, ip: str, session_id: str | None, user: str, status: str, details: str) -> None:
        line = json.dumps({
            "timestamp": get_current_time().isoformat(),
            "ip": ip,
            "session": session_id[:8] if session_id else "unknown",
            "user": user,
            "status": status,
            "details": details,
        }) + "\n"
        with self.lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
            self._size += len(line.encode("utf-8"))
            if self._size > self.max_bytes or time.monotonic() - self._pruned_at > _PRUNE_EVERY:
                self.prune()

    def prune(self) -> int:
        """Drop the entries older than the retention, then the oldest until the log fits. Returns how many."""
        with self.lock:
            lines = self._read_lines()
            cutoff = get_current_time() - self.retention
            kept = [line for line in lines if _parse(line) and not _older_than(line, cutoff)]
            sizes = [len(line.encode("utf-8")) for line in kept]
            total = sum(sizes)
            if total > self.max_bytes:
                budget, drop = self.max_bytes * _FILL_AFTER_PRUNE, 0
                while total > budget and drop < len(kept):
                    total -= sizes[drop]
                    drop += 1
                kept = kept[drop:]
            if len(kept) != len(lines):
                self._rewrite(kept)
            self._size = total
            self._pruned_at = time.monotonic()
            return len(lines) - len(kept)

    def tail(self, limit: int = 500) -> list[dict]:
        """The newest *limit* entries, oldest first, reading only the end of the file."""
        try:
            with open(self.path, "rb") as f:
                size = f.seek(0, os.SEEK_END)
                window = min(size, limit * _BYTES_PER_ENTRY)
                f.seek(size - window)
                if window < size:
                    f.readline()  # skip the partial first line
                text = f.read().decode("utf-8", errors="replace")
        except FileNotFoundError:
            return []
        return [row for row in map(_parse, text.splitlines()) if row][-limit:]

    def clear(self, *, test_only: bool = False) -> tuple[int, int]:
        """Remove every entry, or only the test-mode ones. Returns ``(removed, kept)``."""
        with self.lock:
            lines = self._read_lines()
            kept = [line for line in lines if test_only and not _is_test_entry(line)]
            self._rewrite(kept)
            self._size = sum(len(line.encode("utf-8")) for line in kept)
        return len(lines) - len(kept), len(kept)

    def _read_lines(self, path: str | None = None) -> list[str]:
        try:
            with open(path or self.path, encoding="utf-8", errors="replace") as f:
                return [line if line.endswith("\n") else line + "\n" for line in f]
        except FileNotFoundError:
            return []

    def _rewrite(self, lines: list[str]) -> None:
        """Replace the file's contents in one step, so a crash leaves the old log or the new one."""
        fd, tmp = tempfile.mkstemp(prefix=".log-", dir=os.path.dirname(self.path) or ".")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.writelines(lines)
            # Keep the log's permissions as they were (private, unless someone chose otherwise).
            try:
                mode = stat.S_IMODE(os.stat(self.path).st_mode)
            except FileNotFoundError:
                mode = 0o600
            os.chmod(tmp, mode)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def _fold_old_rotations(self) -> None:
        rotated = [f"{self.path}.{n}" for n in range(_OLD_ROTATIONS, 0, -1) if os.path.exists(f"{self.path}.{n}")]
        if not rotated:
            return
        lines = [line for path in rotated for line in self._read_lines(path)] + self._read_lines()
        self._rewrite(lines)
        for path in rotated:
            os.remove(path)


def _older_than(line: str, cutoff: datetime) -> bool:
    row = _parse(line)
    try:
        moment = datetime.fromisoformat(str(row["timestamp"]).replace(",", "."))
    except (TypeError, ValueError):
        return False  # keep what cannot be dated rather than guess
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=cutoff.tzinfo)
    return moment < cutoff


def _parse(line: str) -> dict | None:
    """One log line as a dashboard row, or ``None`` if it is not an entry.

    Reads the JSON format and the older 'timestamp - ip - user - status - details' one.
    """
    start = line.find("{")
    if start == -1:
        parts = line.rstrip("\n").split(" - ", 4)
        return _row(*parts) if len(parts) >= 4 else None
    try:
        entry = json.loads(line[start:])
    except json.JSONDecodeError:
        return None
    if not isinstance(entry, dict):
        return None
    return _row(*(entry.get(k) for k in ("timestamp", "ip", "user", "status", "details")))


def _row(timestamp, ip, user, status, details=None) -> dict:
    return {
        "timestamp": timestamp,
        "ip": ip,
        "user": None if user == "UNKNOWN" else user,
        "status": status,
        "details": details,
    }


def _is_test_entry(line: str) -> bool:
    row = _parse(line)
    return bool(row) and _TEST_MODE_MARKER in str(row["details"])
