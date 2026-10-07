"""The email queue: sent once, retried on a schedule, given up on cleanly."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.outbox import BACKOFF_MINUTES, Dispatcher, Emitter, Sealer
from app.domain.store import Store, now_iso
from app.mail.mailer import FileMailer, MailError


class Flaky:
    name = "flaky"

    def __init__(self, error: MailError | None = None):
        self.error, self.sent = error, []

    def send(self, mail):
        if self.error:
            raise self.error
        self.sent.append(mail)
        return f"ref-{len(self.sent)}"


@pytest.fixture
def queue(tmp_path):
    store = Store(tmp_path / "store.json")
    sealer = Sealer(b"k" * 32)
    request = {"id": "req_1", "version": 1, "type": "ONBOARDING", "status": "PENDING_MANAGER",
               "subject": {"displayName": "Nadia Putri", "department": "IT — Engineering"},
               "requester": {"name": "Ayu", "email": "ayu@example.com"},
               "payload": {"kind": "ONBOARDING", "firstName": "Nadia", "lastName": "Putri", "displayName": "Nadia Putri",
                           "email": "nadia@example.com", "userId": "nadia", "jobTitle": "Backend Engineer",
                           "department": "IT — Engineering", "employmentType": "PERMANENT", "locationType": "PUSAT",
                           "managerName": "Sarah", "managerEmail": "sarah@example.com", "startDate": "2026-10-07"},
               "approvals": [{"stage": "MANAGER", "version": 1, "approver": {"name": "Sarah", "email": "sarah@example.com"}}],
               "effectiveAt": None, "createdAt": now_iso()}

    def seed(data):
        data["lifecycleRequests"].append(request)
        Emitter(sealer, 24).approval_request(data, request, "MANAGER")

    store.mutate(seed)
    return store, sealer


def events(store):
    return store.read()["outboxEvents"]


def make_due(store):
    def due(data):
        for event in data["outboxEvents"]:
            event["nextAttemptAt"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()

    store.mutate(due)


def test_a_sent_message_drops_its_sealed_content(queue, tmp_path):
    store, sealer = queue
    mailer = FileMailer(tmp_path / "mail")
    report = Dispatcher(store, sealer, lambda: mailer, "http://portal").dispatch_due()
    assert report["sent"] == 1
    assert events(store)[0]["state"] == "SENT" and "sealedPayload" not in events(store)[0]
    html = next((tmp_path / "mail").glob("*.html")).read_text(encoding="utf-8")
    assert "http://portal/persetujuan/" in html and "putusan=setuju" in html
    # Nothing is sent twice.
    assert Dispatcher(store, sealer, lambda: mailer, "http://portal").dispatch_due()["attempted"] == 0


def test_a_passing_failure_is_retried_on_the_schedule_then_given_up(queue):
    store, sealer = queue
    dispatcher = Dispatcher(store, sealer, lambda: Flaky(MailError("TRANSIENT", "server sibuk")), "http://portal")
    for attempt in range(1, len(BACKOFF_MINUTES)):
        assert dispatcher.dispatch_due()["retried"] == 1
        event = events(store)[0]
        wait = datetime.fromisoformat(event["nextAttemptAt"]) - datetime.now(timezone.utc)
        assert wait >= timedelta(minutes=BACKOFF_MINUTES[attempt - 1]) - timedelta(seconds=5)
        assert dispatcher.dispatch_due()["attempted"] == 0  # not before its time
        make_due(store)
    assert dispatcher.dispatch_due()["dead"] == 1
    assert events(store)[0]["state"] == "DEAD" and "sealedPayload" not in events(store)[0]


def test_a_refused_recipient_is_not_retried(queue):
    store, sealer = queue
    report = Dispatcher(store, sealer, lambda: Flaky(MailError("RECIPIENT_REJECTED", "no such user")), "x").dispatch_due()
    assert report["dead"] == 1


def test_a_broken_mail_setup_keeps_the_queue_waiting(queue):
    store, sealer = queue

    def broken():
        raise MailError("CONFIG", "EMAIL_DRIVER belum diisi.")

    report = Dispatcher(store, sealer, broken, "x").dispatch_due()
    assert report["error"] == "EMAIL_DRIVER belum diisi." and report["attempted"] == 0
    event = events(store)[0]
    assert event["state"] == "PENDING" and event["attempt"] == 0 and event.get("sealedPayload")
    assert Dispatcher(store, sealer, lambda: Flaky(), "x").dispatch_due()["sent"] == 1


def test_a_different_key_cannot_open_the_queue(queue):
    store, _ = queue
    report = Dispatcher(store, Sealer(b"z" * 32), lambda: Flaky(), "x").dispatch_due()
    assert report["dead"] == 1
    assert "OUTBOX_ENCRYPTION_KEY" in events(store)[0]["lastError"]


def test_the_queue_does_not_hold_the_content_in_the_clear(queue, tmp_path):
    raw = (tmp_path / "store.json").read_text(encoding="utf-8")
    assert "Backend Engineer" not in raw.split('"outboxEvents"')[1].split('"emailDeliveries"')[0]
