"""The portal's background work, on threads inside the one portal process.

  outbox      every OUTBOX_POLL_SECONDS (30), and at once when mail is queued
  worker      every WORKER_POLL_SECONDS (30), and at once after an approval
  AD sync     at start and every AD_SYNC_MINUTES (15)
  sessions    every SESSION_RECHECK_MINUTES (5): sign out whoever AD no longer lets in

Each loop is single-flight and survives its own failures: one bad pass is
logged, and the next one tries again. Nothing runs when the directory is not
configured — the portal still serves pages, and the work waits.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

log = logging.getLogger("background")


def minutes_or_seconds(raw: str | None, default: int, name: str) -> int | None:
    value = (raw or "").strip().lower()
    if not value:
        return default
    if value in ("0", "off"):
        return None
    try:
        number = int(value)
    except ValueError:
        log.error("%s=%r bukan bilangan bulat; memakai %s.", name, raw, default)
        return default
    return number if number > 0 else None


class Loop:
    """Runs `task` every `interval` seconds, or sooner when kicked."""

    def __init__(self, name: str, interval: int, task: Callable[[], None], first_delay: float = 2.0):
        self.name = name
        self.interval = interval
        self.task = task
        self.first_delay = first_delay
        self._kick = threading.Event()
        self._lock = threading.Lock()  # one pass at a time, whoever starts it
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"hc-{name}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def kick(self) -> None:
        self._kick.set()

    def run_now(self) -> None:
        """From the status page: a pass at once, even when the loop is switched off."""
        if self._thread.is_alive():
            self.kick()
        else:
            threading.Thread(target=self._pass, name=f"hc-{self.name}-now", daemon=True).start()

    def _pass(self) -> None:
        with self._lock:
            try:
                self.task()
            except Exception:  # one failed pass must not end the loop
                log.exception("[%s] satu putaran gagal; dicoba lagi nanti.", self.name)

    def stop(self) -> None:
        self._stop.set()
        self._kick.set()

    def _run(self) -> None:
        self._stop.wait(self.first_delay)
        while not self._stop.is_set():
            self._kick.clear()
            self._pass()
            self._kick.wait(self.interval)
