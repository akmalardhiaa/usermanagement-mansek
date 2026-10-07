"""The gap between a correct password and a correct code.

The password step proves the person to AD and grants nothing: no session
exists until the code is right. What survives between the two steps is this
record, found by an id in a short-lived cookie — five minutes, five wrong
codes, and one half-finished sign-in per person.

In memory, not on disk: a restart simply means typing the password again.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass

PENDING_COOKIE = "hc_login_step"
TTL_SECONDS = 5 * 60
MAX_ATTEMPTS = 5


@dataclass
class PendingLogin:
    user: dict
    mode: str  # "enroll" (first sign-in, secret being confirmed) or "verify"
    secret: str | None
    expires_at: float
    attempts: int = 0


def _hash(step_id: str) -> str:
    return hashlib.sha256(step_id.encode()).hexdigest()


class PendingLogins:
    def __init__(self) -> None:
        self._items: dict[str, PendingLogin] = {}
        self._lock = threading.Lock()

    def _sweep(self, now: float) -> None:
        for key in [k for k, v in self._items.items() if v.expires_at <= now]:
            del self._items[key]

    def begin(self, user: dict, mode: str, secret: str | None, now: float | None = None) -> str:
        now = time.time() if now is None else now
        step_id = secrets.token_urlsafe(32)
        with self._lock:
            self._sweep(now)
            # A second password entry replaces the first: an abandoned QR code
            # stops being valid.
            for key in [k for k, v in self._items.items() if v.user["id"] == user["id"]]:
                del self._items[key]
            self._items[_hash(step_id)] = PendingLogin(user, mode, secret, now + TTL_SECONDS)
        return step_id

    def find(self, step_id: str | None, now: float | None = None) -> PendingLogin | None:
        if not step_id:
            return None
        now = time.time() if now is None else now
        with self._lock:
            self._sweep(now)
            return self._items.get(_hash(step_id))

    def fail(self, step_id: str) -> bool:
        """Counts a wrong code; True when that was the last allowed and the step is gone."""
        with self._lock:
            key = _hash(step_id)
            item = self._items.get(key)
            if item is None:
                return True
            item.attempts += 1
            if item.attempts >= MAX_ATTEMPTS:
                del self._items[key]
                return True
            return False

    def end(self, step_id: str | None) -> None:
        if step_id:
            with self._lock:
                self._items.pop(_hash(step_id), None)
