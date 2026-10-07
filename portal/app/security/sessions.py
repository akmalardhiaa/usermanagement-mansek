"""Server-side sessions.

The cookie carries an opaque random id; the file keeps only its SHA-256, so a
leaked copy of the file is not a set of usable sessions. Three things end a
session: an absolute ceiling no activity extends, an idle window activity
pushes forward (never past that ceiling), and revocation, which is immediate.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from app.store.files import mutate_json, read_json

SESSION_COOKIE = "hc_session"
# How stale lastSeen may get before a read bothers to write.
TOUCH_THROTTLE_SECONDS = 60


@dataclass(frozen=True)
class Session:
    user_id: str
    username: str
    email: str
    full_name: str
    department: str | None
    roles: tuple[str, ...]
    created_at: float
    absolute_expires_at: float


def _hash(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _empty() -> dict:
    return {"sessions": []}


def _live(record: dict, now: float) -> bool:
    return not record.get("revokedAt") and record["absoluteExpiresAt"] > now and record["idleExpiresAt"] > now


class SessionStore:
    def __init__(self, path: Path, idle_minutes: int, absolute_minutes: int):
        self._path = path
        self._idle = idle_minutes * 60
        self._absolute = absolute_minutes * 60

    @property
    def max_age(self) -> int:
        return self._absolute

    def create(self, user: dict, now: float | None = None) -> str:
        now = time.time() if now is None else now
        session_id = secrets.token_urlsafe(32)

        def add(data: dict) -> None:
            # A person whose roles changed since another session is signed out
            # there too: a demotion takes effect everywhere.
            for record in data["sessions"]:
                if record["userId"] == user["id"] and _live(record, now) and sorted(record["roles"]) != sorted(user["roles"]):
                    record["revokedAt"] = now
                    record["revokedReason"] = "roles-changed"
            data["sessions"].append(
                {
                    "idHash": _hash(session_id),
                    "userId": user["id"],
                    "username": user["username"],
                    "email": user["email"],
                    "fullName": user["fullName"],
                    "department": user.get("department"),
                    "roles": list(user["roles"]),
                    "createdAt": now,
                    "absoluteExpiresAt": now + self._absolute,
                    "idleExpiresAt": now + self._idle,
                    "lastSeenAt": now,
                }
            )
            # Anything long dead is dropped on the way past.
            data["sessions"] = [r for r in data["sessions"] if r["absoluteExpiresAt"] > now - 86_400]

        mutate_json(self._path, _empty, add)
        return session_id

    def resolve(self, session_id: str | None, now: float | None = None) -> Session | None:
        if not session_id:
            return None
        now = time.time() if now is None else now
        wanted = _hash(session_id)
        record = next(
            (r for r in read_json(self._path, _empty)["sessions"] if hmac.compare_digest(r["idHash"], wanted)), None
        )
        if record is None or not _live(record, now) or not record["roles"]:
            return None
        if now - record["lastSeenAt"] > TOUCH_THROTTLE_SECONDS:
            self._touch(wanted, now)
        return Session(
            record["userId"],
            record["username"],
            record["email"],
            record["fullName"],
            record.get("department"),
            tuple(record["roles"]),
            record["createdAt"],
            record["absoluteExpiresAt"],
        )

    def _touch(self, id_hash: str, now: float) -> None:
        def touch(data: dict) -> None:
            for record in data["sessions"]:
                if record["idHash"] == id_hash and not record.get("revokedAt"):
                    record["lastSeenAt"] = now
                    record["idleExpiresAt"] = min(now + self._idle, record["absoluteExpiresAt"])

        mutate_json(self._path, _empty, touch)

    def revoke(self, session_id: str | None, reason: str = "logout") -> None:
        if not session_id:
            return
        wanted = _hash(session_id)

        def revoke(data: dict) -> None:
            for record in data["sessions"]:
                if record["idHash"] == wanted and not record.get("revokedAt"):
                    record["revokedAt"] = time.time()
                    record["revokedReason"] = reason

        mutate_json(self._path, _empty, revoke)
