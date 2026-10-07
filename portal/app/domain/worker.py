"""Carries out approved requests in Active Directory, through the Java service.

One request at a time per pass, each under a lease: a worker that dies
mid-run leaves its lease to expire, and the request is marked failed with a
note to compare the directory with the checkpoint before retrying — never
re-run blindly. Steps already done are skipped on a retry. Before anything
runs, the request's fingerprint must match what was approved, and the
employee's record must not have changed since (drift).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from app.ad_client import AdClient, AdError
from app.domain.catalog import CatalogError, FIXED_TERM
from app.domain.lifecycle import assert_transition, hash_payload
from app.domain.plan import account_name_for, build_plan, verify
from app.domain.store import Store, audit, new_id, now_iso, record_activity

MAX_ATTEMPTS = 3
LEASE_SECONDS = 60
ERROR_CODES = {
    "PERMISSION": "AD_PERMISSION", "CONFLICT": "AD_CONFLICT", "NOT_FOUND": "AD_NOT_FOUND",
    "TIMEOUT_AFTER_WRITE": "AD_TIMEOUT_AFTER_WRITE", "TRANSIENT": "AD_UNAVAILABLE",
    "NOT_CONFIGURED": "AD_NOT_CONFIGURED",
}


class Worker:
    def __init__(self, store: Store, ad: AdClient, env: Mapping[str, str] | None = None, worker_id: str | None = None):
        self.store = store
        self.ad = ad
        self.env = os.environ if env is None else env
        self.worker_id = worker_id or f"worker-{os.getpid()}"

    # -------------------------------------------------------------- claiming

    def _claim(self, at: datetime) -> dict | None:
        def claim(data: dict) -> dict | None:
            def due(request: dict) -> bool:
                if request["status"] == "QUEUED":
                    return True
                if request["status"] != "SCHEDULED":
                    return False
                effective = request.get("effectiveAt")
                return request["type"] == "ONBOARDING" or not effective or datetime.fromisoformat(effective) <= at

            request = next((r for r in data["lifecycleRequests"] if due(r)), None)
            if request is None:
                return None
            if request["status"] == "SCHEDULED":
                request["status"] = "QUEUED"
            assert_transition(request["status"], "EXECUTING")
            request["status"], request["updatedAt"] = "EXECUTING", now_iso()
            operation = f"{request['id']}:{request['version']}"
            job = next((j for j in data["executionJobs"] if j["operationId"] == operation), None)
            if job is None:
                job = {"operationId": operation, "requestId": request["id"], "version": request["version"],
                       "payloadHash": request["payloadHash"], "state": "CLAIMED", "attempt": 0, "steps": [],
                       "createdAt": now_iso(), "updatedAt": now_iso()}
                data["executionJobs"].append(job)
            job["attempt"] += 1
            job.update(state="RUNNING", leaseWorkerId=self.worker_id, updatedAt=now_iso(),
                       leaseExpiresAt=(at + timedelta(seconds=LEASE_SECONDS)).isoformat())
            audit(data, self.worker_id, "AD Worker", "WORKER", "execution.claimed", request["id"],
                  {"operationId": operation, "attempt": job["attempt"]})
            employee = next((e for e in data["employees"] if e["id"] == request.get("employeeId")), None)
            return json.loads(json.dumps({"request": request, "job": job, "employee": employee}))

        return self.store.mutate(claim)

    def _reclaim_expired(self, at: datetime) -> int:
        def reclaim(data: dict) -> int:
            count = 0
            for request in data["lifecycleRequests"]:
                if request["status"] != "EXECUTING":
                    continue
                job = next((j for j in data["executionJobs"] if j["operationId"] == f"{request['id']}:{request['version']}"), None)
                if job and job.get("leaseExpiresAt") and datetime.fromisoformat(job["leaseExpiresAt"]) > at:
                    continue
                request.update(status="FAILED", updatedAt=at.isoformat())
                if job:
                    job.update(state="FAILED", errorCode="LEASE_EXPIRED", finishedAt=at.isoformat(),
                               errorMessage="Worker berhenti tanpa melaporkan hasil. Bandingkan objek direktori dengan checkpoint sebelum mencoba lagi.")
                audit(data, "system", "HC Portal", "SYSTEM", "execution.lease_expired", request["id"])
                count += 1
            return count

        return self.store.mutate(reclaim)

    # --------------------------------------------------------------- running

    def run_due(self, limit: int = 25) -> dict:
        at = datetime.now(timezone.utc)
        report = {"ran": 0, "completed": 0, "failed": 0, "reclaimed": self._reclaim_expired(at), "outcomes": []}
        for _ in range(limit):
            claim = self._claim(at)
            if claim is None:
                break
            outcome = self._run_one(claim)
            report["ran"] += 1
            report["completed" if outcome["status"] == "COMPLETED" else "failed"] += 1
            report["outcomes"].append(outcome)
        return report

    def _run_one(self, claim: dict) -> dict:
        request, job, employee = claim["request"], claim["job"], claim["employee"]
        done_keys = {s["stepKey"] for s in job["steps"] if s["state"] == "DONE"}
        steps = [s for s in job["steps"] if s["state"] == "DONE"]

        def fail(code: str, message: str, guid: str | None = None) -> dict:
            self._finish_failed(claim, steps, code, message, guid)
            return {"requestId": request["id"], "status": "FAILED", "errorCode": code, "errorMessage": message}

        if hash_payload(request["payload"]) != job["payloadHash"]:
            return fail("PAYLOAD_MISMATCH", "Isi pengajuan tidak cocok dengan yang disetujui.")
        drift = self._drift(request, employee)
        if drift:
            return fail("DRIFT", drift)

        guid = job.get("targetGuid") or (employee or {}).get("objectGUID")
        existing = request["payload"]["kind"] != "ONBOARDING"
        try:
            if existing and not guid and employee:
                adopted = self.ad.find_by_name(account_name_for(employee["email"]))
                guid = adopted["objectGUID"] if adopted else None
            if existing and not guid:
                return fail("AD_NOT_FOUND", f"{(employee or {}).get('displayName', 'Karyawan')} belum tertaut ke objek direktori mana pun, sehingga tidak ada yang bisa diubah.")
            before = self.ad.find_by_guid(guid) if guid else None
            if existing and not before:
                return fail("AD_NOT_FOUND", f"Objek direktori {guid} tidak ditemukan. Akun mungkin sudah dihapus atau dipindahkan di luar aplikasi ini.", guid)
            plan = build_plan(request["payload"], before, self.env)
        except AdError as error:
            return fail(ERROR_CODES.get(error.kind, "UNKNOWN"), str(error), guid)
        except CatalogError as error:
            return fail("AD_NOT_CONFIGURED", str(error), guid)

        for step in plan["steps"]:
            if step["key"] in done_keys:
                continue
            try:
                created = self._apply(step, guid, request["payload"])
                guid = created or guid
                steps.append({"stepKey": step["key"], "state": "DONE", "at": now_iso()})
            except AdError as error:
                code = ERROR_CODES.get(error.kind, "UNKNOWN")
                steps.append({"stepKey": step["key"], "state": "FAILED", "errorCode": code,
                              "errorMessage": str(error), "at": now_iso()})
                suffix = f" (percobaan {job['attempt']}/{MAX_ATTEMPTS})" if error.retryable and job["attempt"] < MAX_ATTEMPTS else ""
                return fail(code, f"{error}{suffix}", guid)

        try:
            observed = self.ad.find_by_guid(guid) if guid else None
        except AdError as error:
            return fail(ERROR_CODES.get(error.kind, "UNKNOWN"), f"Gagal membaca ulang hasil: {error}", guid)
        mismatch = verify(plan, observed)
        if mismatch:
            return fail("VERIFY_FAILED", mismatch, guid)
        self._finish_succeeded(claim, observed, steps)
        return {"requestId": request["id"], "status": "COMPLETED", "objectGUID": observed["objectGUID"]}

    def _apply(self, step: dict, guid: str | None, payload: dict) -> str | None:
        key, params = step["key"], step["params"]
        if key == "create-account":
            name = params["sAMAccountName"]
            domain = (self.env.get("LDAP_DOMAIN") or "").strip()
            created = self.ad.create_account({
                "sAMAccountName": name, "userPrincipalName": f"{name}@{domain}" if domain else payload["email"],
                "displayName": payload["displayName"], "givenName": params.get("givenName"), "sn": params.get("sn"),
                "mail": payload["email"], "department": payload["department"], "title": payload["jobTitle"],
                "manager": account_name_for(payload["managerEmail"]), "ou": params["ou"],
            })
            return created["objectGUID"]
        if key == "set-attributes":
            self.ad.set_attributes(guid, {k: params.get(k) for k in ("displayName", "department", "title", "manager")})
        elif key == "grant-groups" and params.get("groups"):
            self.ad.change_groups(guid, params["groups"], [])
        elif key == "revoke-groups" and params.get("groups"):
            self.ad.change_groups(guid, [], params["groups"])
        elif key == "move-ou":
            self.ad.move(guid, params["ou"])
        elif key == "enable-account":
            self.ad.set_enabled(guid, True)
        elif key == "disable-account":
            self.ad.set_enabled(guid, False)
        return None

    @staticmethod
    def _drift(request: dict, employee: dict | None) -> str | None:
        snapshot = request.get("beforeSnapshot")
        if not snapshot:
            return None
        if not employee:
            return "Catatan karyawan tidak ditemukan lagi."
        for field, expected in snapshot.items():
            current = employee.get(field, "")
            if current != expected:
                return f'{field} berubah sejak pengajuan disetujui: "{expected}" menjadi "{current}".'
        return None

    # ------------------------------------------------------------- finishing

    def _finish_failed(self, claim: dict, steps: list, code: str, message: str, guid: str | None) -> None:
        def write(data: dict) -> None:
            request = next((r for r in data["lifecycleRequests"] if r["id"] == claim["request"]["id"]), None)
            job = next((j for j in data["executionJobs"] if j["operationId"] == claim["job"]["operationId"]), None)
            if not request or not job or job.get("leaseWorkerId") != self.worker_id:
                return  # a stale lease reports nothing
            assert_transition(request["status"], "FAILED")
            request.update(status="FAILED", updatedAt=now_iso())
            job.update(state="FAILED", steps=steps, errorCode=code, errorMessage=message, finishedAt=now_iso(), updatedAt=now_iso())
            if guid:
                job["targetGuid"] = guid
            audit(data, self.worker_id, "AD Worker", "WORKER", "execution.failed", request["id"],
                  {"errorCode": code, "attempt": job["attempt"]})

        self.store.mutate(write)

    def _finish_succeeded(self, claim: dict, observed: dict, steps: list) -> None:
        def write(data: dict) -> None:
            request = next((r for r in data["lifecycleRequests"] if r["id"] == claim["request"]["id"]), None)
            job = next((j for j in data["executionJobs"] if j["operationId"] == claim["job"]["operationId"]), None)
            if not request or not job or job.get("leaseWorkerId") != self.worker_id:
                return
            assert_transition(request["status"], "COMPLETED")
            at = now_iso()
            request.update(status="COMPLETED", closedAt=at, updatedAt=at)
            job.update(state="SUCCEEDED", steps=steps, targetGuid=observed["objectGUID"], finishedAt=at, updatedAt=at)
            payload = claim["request"]["payload"]
            status = "ACTIVE" if observed["enabled"] else "DISABLED"
            if payload["kind"] == "ONBOARDING":
                employee = {
                    "id": new_id("emp"), "firstName": payload["firstName"], "lastName": payload["lastName"],
                    "displayName": payload["displayName"], "email": payload["email"], "jobTitle": payload["jobTitle"],
                    "jobDescription": payload.get("jobDescription"), "department": payload["department"],
                    "employmentType": payload.get("employmentType"), "expiredDate": payload.get("expiredDate"),
                    "locationType": payload.get("locationType"), "branchName": payload.get("branchName"),
                    "managerName": payload["managerName"], "managerEmail": payload["managerEmail"],
                    "status": status, "objectGUID": observed["objectGUID"], "createdAt": at, "updatedAt": at,
                }
                data["employees"].append(employee)
                request["employeeId"] = employee["id"]
                record_activity(data, f"{request['requester']['name']} (disetujui Manager & CISO)", "user.created",
                                f"Akun {employee['displayName']} dibuat di Active Directory.", employee)
            else:
                employee = next((e for e in data["employees"] if e["id"] == claim["request"].get("employeeId")), None)
                if employee:
                    if payload["kind"] == "MOVEMENT":
                        employee.update(department=payload["toDepartment"], jobTitle=payload["toJobTitle"],
                                        jobDescription=payload.get("toJobDescription"),
                                        managerName=payload["toManagerName"], managerEmail=payload["toManagerEmail"])
                        record_activity(data, request["requester"]["name"], "transfer.requested",
                                        f"{employee['displayName']} pindah ke {payload['toDepartment']}.", employee)
                    elif payload["kind"] == "PROFILE_UPDATE":
                        profile = payload["profile"]
                        employee.update({k: profile.get(k) for k in ("firstName", "lastName", "displayName", "jobTitle",
                                                                      "jobDescription", "department", "employmentType",
                                                                      "locationType", "description")})
                        employee["expiredDate"] = profile.get("expiredDate") if profile.get("employmentType") in FIXED_TERM else None
                        employee["branchName"] = profile.get("branchName") if profile.get("locationType") == "CABANG" else None
                        record_activity(data, f"{request['requester']['name']} (disetujui Manager & CISO)",
                                        "employee.profile_updated",
                                        f"Profil {employee['displayName']} diperbarui — "
                                        + ", ".join(c["label"].lower() for c in payload.get("changes", [])) + ".", employee)
                    else:
                        record_activity(data, request["requester"]["name"], "access.disabled",
                                        f"Akun {employee['displayName']} dinonaktifkan dan dipindah ke karantina.", employee)
                    employee.update(status=status, objectGUID=observed["objectGUID"], updatedAt=at)
            audit(data, self.worker_id, "AD Worker", "WORKER", "execution.completed", request["id"],
                  {"objectGUID": observed["objectGUID"]})

        self.store.mutate(write)
        if claim["request"]["payload"]["kind"] == "ONBOARDING":
            self._user_folder(claim["request"]["payload"])

    def _user_folder(self, payload: dict) -> None:
        """A folder for the new hire's paperwork, when USER_FOLDER_ROOT asks for one."""
        root = (self.env.get("USER_FOLDER_ROOT") or "").strip()
        if not root:
            return
        safe = "".join(c for c in payload["displayName"] if c.isalnum() or c in " .-_").strip() or payload["email"]
        folder = Path(root) / f"{safe} ({payload.get('userId') or account_name_for(payload['email'])})"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "ringkasan.txt").write_text(
                "\n".join(f"{label}: {payload.get(key) or '-'}" for label, key in (
                    ("Nama", "displayName"), ("Email", "email"), ("User ID", "userId"), ("Departemen", "department"),
                    ("Jabatan", "jobTitle"), ("Status kepegawaian", "employmentType"), ("Tanggal mulai", "startDate"),
                    ("Tanggal terakhir bekerja", "expiredDate"), ("Manager", "managerName"))) + "\n",
                encoding="utf-8")
        except OSError as error:
            print(f"[user-folder] tidak bisa dibuat: {error}")
