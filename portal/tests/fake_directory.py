"""An Active Directory in memory, answering like the Java AD service does."""

from __future__ import annotations

import uuid

from app.ad_client import AdError, LoginResult
from app.domain.catalog import same_dn

from conftest import USERS

BASE = "DC=corp,DC=example"
PEOPLE = f"OU=Divisi,OU=People,{BASE}"
QUARANTINE = f"OU=Disabled,{BASE}"
GROUP_BASE = f"CN=APP-Base,OU=Groups,{BASE}"
GROUP_ENGINEERING = f"CN=APP-Engineering,OU=Groups,{BASE}"
CISO_GROUP = f"CN=CISO-Approvers,OU=Groups,{BASE}"

# What the worker needs to know about the directory, as the office .env would say it.
DIRECTORY_ENV = {
    "LDAP_DOMAIN": "corp.example",
    "AD_BASE_DN": BASE,
    "AD_DEPARTMENT_OU_PARENT": PEOPLE,
    "AD_QUARANTINE_OU": QUARANTINE,
    "AD_OU_STANDARD": f"OU=Staff,{PEOPLE}",
    "AD_OU_ENGINEERING": f"OU=Engineering,{PEOPLE}",
    "AD_ACCESS_GROUP_BASE": GROUP_BASE,
    "AD_ACCESS_GROUP_ENGINEERING": GROUP_ENGINEERING,
}


class FakeDirectory:
    def __init__(self) -> None:
        self.accounts: dict[str, dict] = {}  # by GUID
        self.down = False
        self.fail_on: str | None = None  # one write kind that fails, once
        self.writes: list[tuple] = []
        self.ous = {PEOPLE, QUARANTINE, f"OU=Staff,{PEOPLE}", f"OU=Engineering,{PEOPLE}", BASE}
        self.groups: dict[str, list[dict]] = {}

    # ---- seeding
    def add(self, name: str, display: str, mail: str, department: str, ou: str, *, enabled: bool = True,
            manager: str = "", title: str = "Staff", groups: list[str] | None = None) -> dict:
        guid = str(uuid.uuid4())
        given, _, sn = display.partition(" ")
        self.accounts[guid] = {
            "objectGUID": guid, "sAMAccountName": name, "userPrincipalName": f"{name}@corp.example",
            "displayName": display, "givenName": given, "sn": sn, "mail": mail, "department": department,
            "title": title, "manager": manager, "enabled": enabled, "ou": ou, "groups": list(groups or []),
        }
        return self.accounts[guid]

    def _guard(self, write: str | None = None) -> None:
        if self.down or (write and write == self.fail_on):
            if write == self.fail_on:
                self.fail_on = None
            raise AdError("TRANSIENT", "Domain controller tidak bisa dihubungi.")

    def _get(self, guid: str) -> dict:
        account = self.accounts.get(guid.lower())
        if account is None:
            raise AdError("NOT_FOUND", f"Objek {guid} tidak ditemukan.")
        return account

    # ---- what the portal calls
    def login(self, username: str, password: str) -> LoginResult:
        known = USERS.get(username.lower())
        if known and known[0] == password:
            return LoginResult(user=dict(known[1]))
        return LoginResult(code="INVALID_CREDENTIALS", message="Username atau kata sandi salah.")

    def health(self) -> dict:
        return {"ok": True, "directory": "ldaps"}

    def list_accounts(self, base: str) -> list[dict]:
        self._guard()
        return [dict(a) for a in self.accounts.values() if a["ou"].lower().endswith(base.lower())]

    def find_by_name(self, name: str) -> dict | None:
        self._guard()
        return next((dict(a) for a in self.accounts.values() if a["sAMAccountName"].lower() == name.lower()), None)

    def find_by_guid(self, guid: str) -> dict | None:
        self._guard()
        account = self.accounts.get(guid.lower())
        return dict(account) if account else None

    def standing(self, name: str) -> dict:
        self._guard()
        known = USERS.get(name.lower())
        account = self.find_by_name(name)
        if account is None:
            return {"found": False, "enabled": False, "roles": []}
        return {"found": True, "enabled": account["enabled"], "roles": list(known[1]["roles"]) if known else []}

    def group_members(self, dn: str) -> list[dict]:
        self._guard()
        return list(self.groups.get(dn, []))

    def create_account(self, spec: dict) -> dict:
        self._guard("create")
        if spec["ou"] not in self.ous and not spec["ou"].lower().endswith(PEOPLE.lower()):
            raise AdError("NOT_FOUND", f"OU {spec['ou']} tidak ada.")
        if self.find_by_name(spec["sAMAccountName"]):
            raise AdError("CONFLICT", f"{spec['sAMAccountName']} sudah ada.")
        self.writes.append(("create", spec["sAMAccountName"], spec["ou"]))
        manager = spec.get("manager") or ""
        account = self.add(spec["sAMAccountName"], spec["displayName"], spec["mail"], spec["department"], spec["ou"],
                           enabled=False, manager=manager, title=spec.get("title") or "")
        return dict(account)

    def set_attributes(self, guid: str, patch: dict) -> None:
        self._guard("attributes")
        account = self._get(guid)
        self.writes.append(("attributes", guid, dict(patch)))
        for key, value in patch.items():
            if value is None:
                continue
            account[key] = value

    def change_groups(self, guid: str, add: list[str], remove: list[str]) -> None:
        self._guard("groups")
        account = self._get(guid)
        self.writes.append(("groups", guid, list(add), list(remove)))
        held = [g for g in account["groups"] if not any(same_dn(g, r) for r in remove)]
        held += [g for g in add if not any(same_dn(g, h) for h in held)]
        account["groups"] = held

    def move(self, guid: str, ou: str) -> None:
        self._guard("move")
        self.writes.append(("move", guid, ou))
        self._get(guid)["ou"] = ou

    def set_enabled(self, guid: str, enabled: bool) -> None:
        self._guard("enabled")
        self.writes.append(("enabled", guid, enabled))
        self._get(guid)["enabled"] = enabled

    def entry_exists(self, dn: str) -> bool:
        self._guard()
        return any(same_dn(dn, ou) for ou in self.ous) or dn.lower().endswith(PEOPLE.lower())
