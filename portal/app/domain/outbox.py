"""The email outbox: queued with the change that needs it, sent right after.

An approval email carries a raw approval link — a credential — so the queued
message is sealed (AES-256-GCM, OUTBOX_ENCRYPTION_KEY) until it is sent, and
the sealed copy is dropped once a provider accepts it. A failure is retried
after 1, 5, 15, 30 and 60 minutes; a permanent one (a refused recipient, a
rejected login) is not retried, because the identical refusal would follow.
"""

from __future__ import annotations

import base64
import json
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Callable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.domain.lifecycle import REDACTED, answerers, issue_token
from app.domain.store import Store, new_id, now_iso
from app.mail.mailer import MailError, Mailer
from app.mail.render import render_email

BACKOFF_MINUTES = (1, 5, 15, 30, 60)


class Sealer:
    def __init__(self, key: bytes):
        self._aead = AESGCM(key)

    def seal(self, value: dict) -> dict:
        nonce = os.urandom(12)
        sealed = self._aead.encrypt(nonce, json.dumps(value).encode("utf-8"), None)
        return {"nonce": base64.b64encode(nonce).decode(), "ciphertext": base64.b64encode(sealed).decode()}

    def open(self, sealed: dict) -> dict:
        plain = self._aead.decrypt(base64.b64decode(sealed["nonce"]), base64.b64decode(sealed["ciphertext"]), None)
        return json.loads(plain)


def mail_safe(payload: dict) -> dict:
    """The payload minus what must not travel in an email: the termination note,
    and HC's internal profile note."""
    safe = dict(payload)
    if safe.get("kind") == "TERMINATION":
        safe.pop("note", None)
    if safe.get("kind") == "PROFILE_UPDATE":
        safe["profile"] = {k: v for k, v in safe["profile"].items() if k != "description"}
        safe["changes"] = [
            {**c, "from": REDACTED, "to": REDACTED} if c["field"] == "description" else c for c in safe.get("changes", [])
        ]
    return safe


class Emitter:
    """Queues the emails a change in a request owes, inside that same write."""

    def __init__(self, sealer: Sealer, ttl_hours: int):
        self._sealer = sealer
        self._ttl = ttl_hours

    def _push(self, data: dict, kind: str, request: dict, recipient: dict, content: dict, stage: str | None = None) -> None:
        at = now_iso()
        data["outboxEvents"].append({
            "eventId": new_id("out"), "kind": kind, "aggregateId": request["id"], "version": request["version"],
            "stage": stage, "recipient": recipient["email"], "recipientName": recipient["name"],
            "sealedPayload": self._sealer.seal(content), "state": "PENDING", "attempt": 0,
            "nextAttemptAt": at, "createdAt": at, "updatedAt": at,
        })

    def approval_request(self, data: dict, request: dict, stage: str) -> None:
        step = next(s for s in request["approvals"] if s["stage"] == stage and s["version"] == request["version"])
        manager_step = next((s for s in request["approvals"] if s["stage"] == "MANAGER" and s["version"] == request["version"]), None)
        for member in answerers(step):
            raw, record = issue_token(request["id"], request["version"], stage, member["email"], self._ttl)
            data["approvalTokens"].append(record)
            self._push(data, "approval.request", request, member, {
                "token": raw, "requestId": request["id"], "type": request["type"], "stage": stage,
                "version": request["version"], "subjectName": request["subject"]["displayName"],
                "requesterName": request["requester"]["name"], "approverName": member["name"],
                "effectiveAt": request.get("effectiveAt"), "payload": mail_safe(request["payload"]),
                "managerDecision": ({"by": manager_step["decidedBy"]["name"], "at": manager_step["decidedAt"]}
                                    if stage == "CISO" and manager_step and manager_step.get("decidedBy") else None),
            }, stage)

    def result(self, data: dict, request: dict, outcome: str) -> None:
        self._push(data, "approval.result", request, request["requester"], {
            "requestId": request["id"], "type": request["type"], "version": request["version"],
            "subjectName": request["subject"]["displayName"], "outcome": outcome,
            "requesterName": request["requester"]["name"],
        })


class Dispatcher:
    def __init__(self, store: Store, sealer: Sealer, mailer_factory: Callable[[], Mailer], base_url: str):
        self.store = store
        self._sealer = sealer
        self._mailer_factory = mailer_factory
        self._base_url = base_url

    def _claim(self, at: datetime) -> dict | None:
        def claim(data: dict) -> dict | None:
            due = next((e for e in data["outboxEvents"] if e["state"] == "PENDING"
                        and datetime.fromisoformat(e["nextAttemptAt"].replace("Z", "+00:00")) <= at), None)
            if due is None:
                return None
            due["attempt"] += 1
            due["updatedAt"] = now_iso()
            return json.loads(json.dumps(due))

        return self.store.mutate(claim)

    def _record(self, event: dict, outcome: str, error: MailError | None = None, ref: str | None = None) -> str:
        def record(data: dict) -> str:
            stored = next((e for e in data["outboxEvents"] if e["eventId"] == event["eventId"]), None)
            if stored is None:
                return "DEAD"
            at = now_iso()
            delivery = {"eventId": event["eventId"], "attempt": event["attempt"], "recipient": event["recipient"]}
            if outcome == "SENT":
                stored.update(state="SENT", updatedAt=at)
                stored.pop("sealedPayload", None)
                data["emailDeliveries"].append({**delivery, "providerRef": ref, "acceptedAt": at})
                return "SENT"
            data["emailDeliveries"].append({**delivery, "failedAt": at, "errorKind": error.kind, "errorMessage": str(error)})
            stored.update(lastError=str(error), lastErrorKind=error.kind, updatedAt=at)
            if not error.retryable or event["attempt"] >= len(BACKOFF_MINUTES):
                stored["state"] = "DEAD"
                stored.pop("sealedPayload", None)
                return "DEAD"
            minutes = BACKOFF_MINUTES[min(event["attempt"], len(BACKOFF_MINUTES)) - 1]
            stored["nextAttemptAt"] = (datetime.now(timezone.utc) + timedelta(minutes=minutes, seconds=random.randint(0, 30))).isoformat()
            return "RETRY"

        return self.store.mutate(record)

    def _anything_due(self, at: datetime) -> bool:
        return any(e["state"] == "PENDING" and datetime.fromisoformat(e["nextAttemptAt"].replace("Z", "+00:00")) <= at
                   for e in self.store.read()["outboxEvents"])

    def dispatch_due(self, limit: int = 25) -> dict:
        report = {"attempted": 0, "sent": 0, "retried": 0, "dead": 0, "error": None}
        if not self._anything_due(datetime.now(timezone.utc)):
            return report
        # A mail setup that cannot work is the setup's fault, not the messages': they wait for it to be fixed.
        try:
            mailer = self._mailer_factory()
        except MailError as error:
            report["error"] = str(error)
            return report
        for _ in range(limit):
            event = self._claim(datetime.now(timezone.utc))
            if event is None:
                break
            report["attempted"] += 1
            try:
                if not event.get("sealedPayload"):
                    raise MailError("PAYLOAD", "Payload outbox sudah tidak ada, pesan tidak dapat dirender ulang.")
                try:
                    content = self._sealer.open(event["sealedPayload"])
                except Exception as error:
                    raise MailError("PAYLOAD", "Payload outbox tidak dapat didekripsi. Periksa OUTBOX_ENCRYPTION_KEY.") from error
                message = render_email(event["kind"], content, event["recipient"], event.get("recipientName"), self._base_url)
                ref = mailer.send(message)
                outcome = self._record(event, "SENT", ref=ref)
            except MailError as error:
                outcome = self._record(event, "FAIL", error=error)
            except Exception as error:  # anything unforeseen is retried on the schedule, then given up
                outcome = self._record(event, "FAIL", error=MailError("TRANSIENT", f"Kesalahan tak terduga: {error}"))
            report[{"SENT": "sent", "RETRY": "retried", "DEAD": "dead"}[outcome]] += 1
        return report
