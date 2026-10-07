"""The employee directory, filled from Active Directory — read only.

Accounts are matched by objectGUID, or by email for a record from before the
link; nothing is ever deleted. Only an enabled account with a mailbox is
added; an account that has gone from AD is left in place and counted, not
removed. Fields AD owns are copied; fields only the portal keeps are not
touched, and an empty value in AD never blanks one here.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Mapping

from app.ad_client import AdClient
from app.domain.store import Store, new_id, record_activity


def sync_sources(env: Mapping[str, str]) -> list[str]:
    explicit = [part.strip() for part in (env.get("AD_SYNC_OUS") or "").split(";") if part.strip()]
    if explicit:
        return explicit
    base = (env.get("AD_BASE_DN") or env.get("LDAP_BASE_DN") or "").strip()
    return [base] if base else []


def _fields(account: dict, manager: dict | None) -> dict:
    display = (account.get("displayName") or "").strip() or " ".join(
        p for p in (account.get("givenName"), account.get("sn")) if p).strip() or account["sAMAccountName"]
    first, _, rest = display.partition(" ")
    return {
        "firstName": (account.get("givenName") or "").strip() or first,
        "lastName": (account.get("sn") or "").strip() or rest,
        "displayName": display,
        "email": (account.get("mail") or "").strip().lower(),
        "jobTitle": (account.get("title") or "").strip(),
        "department": (account.get("department") or "").strip(),
        "managerName": ((manager or {}).get("displayName") or "").strip(),
        "managerEmail": ((manager or {}).get("mail") or "").strip().lower(),
        "status": "ACTIVE" if account["enabled"] else "DISABLED",
    }


def sync_employees(store: Store, ad: AdClient, env: Mapping[str, str] | None = None) -> dict:
    env = os.environ if env is None else env
    sources = sync_sources(env)
    at = datetime.now(timezone.utc).isoformat()

    by_guid: dict[str, dict] = {}
    for source in sources:
        for account in ad.list_accounts(source):
            if account.get("objectGUID"):
                by_guid[account["objectGUID"].lower()] = account
    accounts = list(by_guid.values())
    by_name: dict[str, dict | None] = {a["sAMAccountName"].lower(): a for a in accounts}
    manager_of: dict[str, dict | None] = {}
    for account in accounts:
        name = (account.get("manager") or "").lower()
        if not name:
            continue
        if name not in by_name:
            try:
                by_name[name] = ad.find_by_name(name)
            except Exception:
                by_name[name] = None
        manager_of[account["objectGUID"].lower()] = by_name[name]

    def write(data: dict) -> dict:
        report = {"at": at, "sources": sources, "read": len(accounts), "created": 0, "updated": 0, "linked": 0,
                  "skipped": 0, "ignoredDisabled": 0, "conflicts": 0, "notFound": 0}
        for account in accounts:
            guid = account["objectGUID"].lower()
            email = (account.get("mail") or "").strip().lower()
            if not email:
                report["skipped"] += 1
                continue
            fields = _fields(account, manager_of.get(guid))
            employee = next((e for e in data["employees"] if (e.get("objectGUID") or "").lower() == guid), None)
            if employee is None:
                same_address = [e for e in data["employees"] if e["email"].strip().lower() == email]
                if len(same_address) > 1 or (same_address and same_address[0].get("objectGUID")):
                    report["conflicts"] += 1
                    continue
                employee = same_address[0] if same_address else None
                if employee:
                    employee.update(objectGUID=account["objectGUID"], updatedAt=at)
                    report["linked"] += 1
            if employee is None:
                if not account["enabled"]:
                    report["ignoredDisabled"] += 1
                    continue
                data["employees"].append({"id": new_id("emp"), **fields, "objectGUID": account["objectGUID"],
                                          "createdAt": at, "updatedAt": at})
                report["created"] += 1
                continue
            changed = False
            for key, value in fields.items():
                if value == "":
                    continue
                current = employee.get(key) or ""
                same = current.lower() == value if key in ("email", "managerEmail") else current == value
                if not same:
                    employee[key] = value
                    changed = True
            if changed:
                employee["updatedAt"] = at
                report["updated"] += 1
        report["notFound"] = sum(1 for e in data["employees"] if e.get("objectGUID") and e["objectGUID"].lower() not in by_guid)
        if report["created"] + report["updated"] + report["linked"]:
            record_activity(data, "Sinkronisasi AD", "directory.synced",
                            f"Dari Active Directory: {describe(report)}.")
        return report

    return store.mutate(write)


def describe(report: dict) -> str:
    return f"{report['created']} karyawan baru, {report['updated']} diperbarui, {report['linked']} ditautkan"
