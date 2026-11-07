"""Audit trail: one JSON line per door or admin event, in a size-rotated file."""

import json
import logging
import os
from logging.handlers import RotatingFileHandler

from config import get_current_time

_TEST_MODE_MARKER = "TEST MODE"
_BYTES_PER_ENTRY = 250  # generous estimate, to read just enough of the file's tail


class AuditLog:
    """The append-only event log that the admin dashboard reads back."""

    def __init__(self, path: str, max_bytes: int = 1_000_000, backups: int = 5):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # A handler of its own rather than a named logger: no global logging state,
        # so two logs (say, the app's and a test's) can never cross wires.
        self._handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
        self._handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))

    def record(self, ip: str, session_id: str | None, user: str, status: str, details: str) -> None:
        entry = json.dumps({
            "timestamp": get_current_time().isoformat(),
            "ip": ip,
            "session": session_id[:8] if session_id else "unknown",
            "user": user,
            "status": status,
            "details": details,
        })
        self._handler.handle(logging.makeLogRecord({"msg": entry, "levelno": logging.INFO, "levelname": "INFO"}))

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
        # The handler's lock keeps any entry from landing between the read and the
        # rewrite. Rewriting in place, rather than swapping in a new file, keeps the
        # handler's append-mode stream pointed at the live file, so logging carries on.
        with self._handler.lock:
            lines = self._read_lines()
            kept = [line for line in lines if test_only and not _is_test_entry(line)]
            with open(self.path, "w", encoding="utf-8") as f:
                f.writelines(kept)
        return len(lines) - len(kept), len(kept)

    def _read_lines(self) -> list[str]:
        try:
            with open(self.path, encoding="utf-8", errors="replace") as f:
                return f.readlines()
        except FileNotFoundError:
            return []


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
