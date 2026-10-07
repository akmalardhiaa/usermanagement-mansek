"""Limits on guessing: per address, and per account.

The per-address limit keys on the client address — the last X-Forwarded-For
entry when IIS sits in front, the socket otherwise. Reached directly, a header
is the client's to write, so the second limit counts failures per ACCOUNT,
which no header can spread out: five wrong passwords or codes in fifteen
minutes lock that account in the portal for fifteen minutes, before AD is
asked again — so the portal cannot be used to lock somebody out of Windows.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

MAX_FAILURES = 5
WINDOW_SECONDS = 15 * 60
LOCK_SECONDS = 15 * 60
LOCK_MINUTES = LOCK_SECONDS // 60


def account_key(username: str) -> str:
    """'CORP\\ayu', 'ayu@corp.example' and 'Ayu' are one account."""
    return username.strip().split("\\")[-1].split("@")[0].lower()


@dataclass
class _Counter:
    failures: int = 0
    first_at: float = 0.0
    locked_until: float = 0.0


@dataclass
class _Window:
    count: int = 0
    reset_at: float = 0.0


@dataclass
class Throttle:
    _accounts: dict[str, _Counter] = field(default_factory=dict)
    _addresses: dict[str, _Window] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # --- per account -----------------------------------------------------
    def locked_for(self, username: str, now: float | None = None) -> int:
        """Seconds until the account's lock lifts; 0 when not locked."""
        now = time.time() if now is None else now
        with self._lock:
            counter = self._accounts.get(account_key(username))
            return max(0, int(counter.locked_until - now)) if counter and counter.locked_until > now else 0

    def fail(self, username: str, now: float | None = None) -> bool:
        """Counts one failure; True when this one locked the account."""
        now = time.time() if now is None else now
        key = account_key(username)
        with self._lock:
            counter = self._accounts.get(key)
            if counter is None or now - counter.first_at > WINDOW_SECONDS or (0 < counter.locked_until <= now):
                counter = _Counter(first_at=now)
                self._accounts[key] = counter
            counter.failures += 1
            if counter.failures >= MAX_FAILURES and not counter.locked_until:
                counter.locked_until = now + LOCK_SECONDS
                return True
            return False

    def clear(self, username: str) -> None:
        with self._lock:
            self._accounts.pop(account_key(username), None)

    # --- per address -----------------------------------------------------
    def allow_address(self, address: str, limit: int = 10, window: int = 300, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            entry = self._addresses.get(address)
            if entry is None or entry.reset_at <= now:
                entry = _Window(reset_at=now + window)
                self._addresses[address] = entry
            entry.count += 1
            return entry.count <= limit

    def reset_address(self, address: str) -> None:
        with self._lock:
            self._addresses.pop(address, None)
