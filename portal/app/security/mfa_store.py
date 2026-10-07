"""Who has an authenticator app connected, with the secret sealed.

The secret is the whole second factor — anyone holding it produces valid
codes — so it is stored sealed with AES-256-GCM under OUTBOX_ENCRYPTION_KEY,
never in the clear. A copied data folder without the key is not a set of
second factors. Change the key and every enrolment stops opening: each person
is then reset (scripts/mfa_reset.py) and connects their phone again.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.store.files import mutate_json, read_json


class MfaUnreadableError(Exception):
    """The stored secret does not open with this key — never treated as 'not enrolled'."""


@dataclass(frozen=True)
class Enrollment:
    user_id: str
    secret: str
    enrolled_at: str
    last_step: int


def _empty() -> dict:
    return {"enrollments": []}


class MfaStore:
    def __init__(self, path: Path, key: bytes):
        self._path = path
        self._aead = AESGCM(key)

    def _seal(self, secret: str) -> dict:
        nonce = os.urandom(12)
        sealed = self._aead.encrypt(nonce, secret.encode("ascii"), None)
        return {"nonce": base64.b64encode(nonce).decode(), "ciphertext": base64.b64encode(sealed).decode()}

    def _open(self, sealed: dict) -> str:
        return self._aead.decrypt(
            base64.b64decode(sealed["nonce"]), base64.b64decode(sealed["ciphertext"]), None
        ).decode("ascii")

    def read(self, user_id: str) -> Enrollment | None:
        key = user_id.lower()
        record = next((entry for entry in read_json(self._path, _empty)["enrollments"] if entry["userId"] == key), None)
        if record is None:
            return None
        try:
            secret = self._open(record["secret"])
        except (InvalidTag, KeyError, ValueError) as error:
            # Not "no enrolment": that would let whoever has the password
            # connect a phone of their own.
            raise MfaUnreadableError(key) from error
        return Enrollment(key, secret, record["enrolledAt"], record["lastStep"])

    def save(self, user_id: str, secret: str, step: int) -> bool:
        """Stores a confirmed enrolment; refuses to replace an existing one."""
        key = user_id.lower()

        def add(data: dict) -> bool:
            if any(entry["userId"] == key for entry in data["enrollments"]):
                return False
            data["enrollments"].append(
                {
                    "userId": key,
                    "secret": self._seal(secret),
                    "enrolledAt": datetime.now(timezone.utc).isoformat(),
                    "lastStep": step,
                }
            )
            return True

        return mutate_json(self._path, _empty, add)

    def consume_step(self, user_id: str, step: int) -> bool:
        """False when this step, or a later one, was already used."""
        key = user_id.lower()

        def consume(data: dict) -> bool:
            record = next((entry for entry in data["enrollments"] if entry["userId"] == key), None)
            if record is None or step <= record["lastStep"]:
                return False
            record["lastStep"] = step
            return True

        return mutate_json(self._path, _empty, consume)

    def remove(self, user_id: str) -> bool:
        key = user_id.lower()

        def drop(data: dict) -> bool:
            before = len(data["enrollments"])
            data["enrollments"] = [entry for entry in data["enrollments"] if entry["userId"] != key]
            return len(data["enrollments"]) != before

        return mutate_json(self._path, _empty, drop)

    def count(self) -> int:
        return len(read_json(self._path, _empty)["enrollments"])
