"""The portal's records, in one JSON file — no database server.

Employees, lifecycle requests, execution jobs, the email outbox, approval
tokens, the audit trail and the activity feed. Read-modify-write under one
lock, written whole and renamed into place. A write that queued mail wakes the
email sender at once (see background.py), here rather than at every place
that queues it, so no new path can forget to.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypeVar

from app.store.files import mutate_json, read_json

T = TypeVar("T")

COLLECTIONS = (
    "employees",
    "lifecycleRequests",
    "executionJobs",
    "outboxEvents",
    "emailDeliveries",
    "approvalTokens",
    "auditEvents",
    "activity",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4()}"


def _empty() -> dict:
    return {name: [] for name in COLLECTIONS}


class Store:
    def __init__(self, path: Path):
        self.path = path
        self._on_mail: Callable[[], None] | None = None

    def on_mail_queued(self, callback: Callable[[], None] | None) -> None:
        self._on_mail = callback

    def read(self) -> dict:
        data = read_json(self.path, _empty)
        for name in COLLECTIONS:
            data.setdefault(name, [])
        return data

    def mutate(self, mutator: Callable[[dict], T]) -> T:
        queued = {"before": 0, "after": 0}

        def run(data: dict) -> T:
            for name in COLLECTIONS:
                data.setdefault(name, [])
            queued["before"] = len(data["outboxEvents"])
            result = mutator(data)
            queued["after"] = len(data["outboxEvents"])
            return result

        result = mutate_json(self.path, _empty, run)
        if queued["after"] > queued["before"] and self._on_mail:
            self._on_mail()
        return result


def record_activity(data: dict, actor: str, action: str, detail: str, employee: dict | None = None) -> None:
    data["activity"].append(
        {
            "id": new_id("act"),
            "at": now_iso(),
            "actor": actor,
            "action": action,
            "employeeId": employee["id"] if employee else None,
            "employeeName": employee["displayName"] if employee else None,
            "detail": detail,
        }
    )


def audit(data: dict, actor_id: str, actor_name: str, source: str, action: str, target: str, detail: dict | None = None) -> None:
    data["auditEvents"].append(
        {
            "id": new_id("evt"),
            "at": now_iso(),
            "actorId": actor_id,
            "actorName": actor_name,
            "source": source,
            "action": action,
            "target": target,
            "correlationId": target,
            "detail": detail or {},
        }
    )


DEMO_ROSTER = [
    ("Ayu", "Prameswari", "ayu.prameswari@example.com", "Head of Human Capital", "Human Capital", "Dimas Anggara", "dimas.anggara@example.com", "ACTIVE"),
    ("Dimas", "Anggara", "dimas.anggara@example.com", "Head of Division", "Human Capital", "", "", "ACTIVE"),
    ("Rizky", "Maulana", "rizky.maulana@example.com", "Senior Backend Engineer", "IT — Engineering", "Sarah Wijaya", "sarah.wijaya@example.com", "ACTIVE"),
    ("Sarah", "Wijaya", "sarah.wijaya@example.com", "Engineering Manager", "IT — Engineering", "Dimas Anggara", "dimas.anggara@example.com", "ACTIVE"),
    ("Bagus", "Nugroho", "bagus.nugroho@example.com", "Security Analyst", "IT — Security", "Dimas Anggara", "dimas.anggara@example.com", "ACTIVE"),
    ("Clara", "Halim", "clara.halim@example.com", "Financial Analyst", "Finance", "Dimas Anggara", "dimas.anggara@example.com", "DISABLED"),
    ("Yoga", "Pratama", "yoga.pratama@example.com", "Product Designer", "IT — Product", "Sarah Wijaya", "sarah.wijaya@example.com", "ACTIVE"),
]


def seed_demo_roster(store: Store) -> None:
    """A few people for a laptop demo with no AD. Never with a real directory."""

    def seed(data: dict) -> None:
        if data["employees"]:
            return
        at = now_iso()
        for first, last, email, title, department, manager, manager_email, status in DEMO_ROSTER:
            data["employees"].append(
                {
                    "id": new_id("emp"),
                    "firstName": first,
                    "lastName": last,
                    "displayName": f"{first} {last}",
                    "email": email,
                    "jobTitle": title,
                    "department": department,
                    "managerName": manager,
                    "managerEmail": manager_email,
                    "employmentType": "PERMANENT",
                    "locationType": "PUSAT",
                    "status": status,
                    "createdAt": at,
                    "updatedAt": at,
                }
            )

    store.mutate(seed)
