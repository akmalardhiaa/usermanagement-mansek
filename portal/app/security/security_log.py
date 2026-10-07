"""Who signed in, who failed to, and what ended their sessions.

One JSON object per line, appended — never a rewrite — and rotated to `.1`
past 5 MB. Never a password and never a code: what happened, to which account
name, from which address. Each line also goes to the console, so it reaches
the container log. Writing here never fails the sign-in it describes.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

ROTATE_BYTES = 5 * 1024 * 1024
_log = logging.getLogger("security")


class SecurityLog:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()

    def record(self, event_type: str, username: str | None = None, ip: str | None = None, detail: str | None = None) -> None:
        entry = {"at": datetime.now(timezone.utc).isoformat(), "type": event_type}
        if username:
            entry["username"] = username
        if ip:
            entry["ip"] = ip
        if detail:
            entry["detail"] = detail
        _log.info(
            "[security] %s%s%s%s",
            event_type,
            f" {username}" if username else "",
            f" dari {ip}" if ip else "",
            f" — {detail}" if detail else "",
        )
        try:
            with self._lock:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                if self._path.exists() and self._path.stat().st_size > ROTATE_BYTES:
                    os.replace(self._path, self._path.with_name(self._path.name + ".1"))
                with self._path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            _log.exception("catatan keamanan tidak bisa ditulis")

    def recent(self, limit: int = 50) -> list[dict]:
        try:
            with self._lock:
                lines = self._path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        events: list[dict] = []
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a line cut short by a crash
            if len(events) >= limit:
                break
        return events
