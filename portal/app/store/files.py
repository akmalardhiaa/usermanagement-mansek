"""JSON files as the portal's storage — no database server.

Each file is read and rewritten whole under a lock, and written to a temporary
file first and renamed over the old one, so a crash mid-write never leaves a
truncated file behind. One portal process only: the locks are per process,
which is why the deployment never runs two portals on the same data.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")

_locks: dict[Path, threading.RLock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    resolved = path.resolve()
    with _locks_guard:
        return _locks.setdefault(resolved, threading.RLock())


def _read(path: Path, default: Callable[[], Any]) -> Any:
    try:
        # A byte-order mark is what Notepad and PowerShell 5.1 put in front of UTF-8.
        return json.loads(path.read_text(encoding="utf-8").lstrip("﻿"))
    except FileNotFoundError:
        return default()


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass  # Windows: ACLs, not modes, protect the folder.
    os.replace(tmp, path)


def read_json(path: Path, default: Callable[[], Any]) -> Any:
    with _lock_for(path):
        return _read(path, default)


def mutate_json(path: Path, default: Callable[[], Any], mutator: Callable[[Any], T]) -> T:
    """Read-modify-write under the file's lock; the mutator edits the value in place."""
    with _lock_for(path):
        value = _read(path, default)
        result = mutator(value)
        _write(path, value)
        return result
