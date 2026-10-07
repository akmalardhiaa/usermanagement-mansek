"""Lifecycle requests: drafted by HC, approved by the manager and then the CISO
team from email, carried out in AD by the worker.

Ported from the TypeScript service, rule for rule: the state machine, routing
(manager from the request, CISO team from AD or configuration, first answer
wins), separation of duties, single-use approval links bound to one stage of
one version, the payload fingerprint the approvers saw, revision, cancel and
retry.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping

from app.domain.catalog import FIXED_TERM
from app.domain.store import Store, audit, new_id, now_iso
from app.domain.validation import JAKARTA

POLICY_VERSION = "2026-09-16.1"

TRANSITIONS: dict[str, tuple[str, ...]] = {
    "DRAFT": ("PENDING_MANAGER", "CANCELLED"),
    "PENDING_MANAGER": ("PENDING_CISO", "REJECTED", "CANCELLED", "EXPIRED", "DRAFT"),
    "PENDING_CISO": ("APPROVED", "REJECTED", "CANCELLED", "EXPIRED", "DRAFT"),
    "APPROVED": ("SCHEDULED", "QUEUED", "CANCELLED"),
    "SCHEDULED": ("QUEUED", "CANCELLED", "EXPIRED"),
    "QUEUED": ("EXECUTING", "CANCELLED"),
    "EXECUTING": ("COMPLETED", "FAILED"),
    "FAILED": ("QUEUED",),
    "COMPLETED": (),
    "REJECTED": (),
    "CANCELLED": (),
    "EXPIRED": (),
}
TERMINAL = {"COMPLETED", "REJECTED", "CANCELLED", "EXPIRED"}

STATUS_LABELS = {
    "DRAFT": "Draf", "PENDING_MANAGER": "Menunggu manager", "PENDING_CISO": "Menunggu CISO",
    "APPROVED": "Disetujui", "SCHEDULED": "Terjadwal", "QUEUED": "Dalam antrean", "EXECUTING": "Sedang dijalankan",
    "COMPLETED": "Selesai", "FAILED": "Gagal", "REJECTED": "Ditolak", "CANCELLED": "Dibatalkan", "EXPIRED": "Kedaluwarsa",
}
TYPE_LABELS = {"ONBOARDING": "Onboarding", "MOVEMENT": "Movement", "TERMINATION": "Termination",
               "PROFILE_UPDATE": "Perubahan profil"}

PROFILE_FIELD_LABELS = {
    "firstName": "Nama depan", "lastName": "Nama belakang", "displayName": "Nama lengkap", "department": "Departemen",
    "jobTitle": "Jabatan", "jobDescription": "Keterangan jabatan", "employmentType": "Status kepegawaian",
    "expiredDate": "Tanggal terakhir bekerja", "locationType": "Lokasi penempatan", "branchName": "Nama cabang",
    "description": "Catatan HC",
}
INTERNAL_PROFILE_FIELDS = {"description"}
REDACTED = "(catatan internal — isinya tidak dikirim lewat email)"

TOKEN_REJECTIONS = {
    "UNKNOWN": "Tautan tidak dikenali.",
    "EXPIRED": "Tautan sudah kedaluwarsa. Minta HC mengirim ulang.",
    "CONSUMED": "Tautan ini sudah dipakai. Keputusan sebelumnya tetap berlaku.",
    "REVOKED": "Tautan sudah tidak berlaku karena pengajuan direvisi, dibatalkan, atau selesai.",
    "WRONG_STAGE": "Tautan ini bukan untuk tahap yang sedang menunggu.",
    "WRONG_VERSION": "Pengajuan sudah direvisi. Tautan ini menunjuk versi lama.",
}


class LifecycleError(Exception):
    """code: NOT_FOUND, CONFLICT, FORBIDDEN, INVALID."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class RoutingError(LifecycleError):
    def __init__(self, message: str):
        super().__init__("INVALID", message)


# --------------------------------------------------------------- state machine

def assert_transition(current: str, to: str) -> None:
    if to not in TRANSITIONS[current]:
        raise LifecycleError("CONFLICT", f"Perubahan status dari {current} ke {to} tidak diizinkan.")


def is_active(status: str) -> bool:
    return status not in TERMINAL


def stage_awaiting(status: str) -> str | None:
    return {"PENDING_MANAGER": "MANAGER", "PENDING_CISO": "CISO"}.get(status)


# ---------------------------------------------------------------- fingerprints

def _canonical(value):
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value) if value[key] is not None}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def hash_payload(payload: dict) -> str:
    text = json.dumps(_canonical(payload), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------- tokens

def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue_token(request_id: str, version: int, stage: str, recipient: str, ttl_hours: int) -> tuple[str, dict]:
    raw = secrets.token_urlsafe(32)
    created = datetime.now(timezone.utc)
    return raw, {
        "tokenHash": hash_token(raw),
        "requestId": request_id,
        "version": version,
        "stage": stage,
        "recipient": recipient.strip().lower(),
        "expiresAt": (created + timedelta(hours=ttl_hours)).isoformat(),
        "createdAt": created.isoformat(),
    }


def _find_token(records: list[dict], raw: str) -> dict | None:
    presented = hash_token(raw)
    return next((r for r in records if hmac.compare_digest(r["tokenHash"], presented)), None)


def _check_token(record: dict, request: dict) -> str | None:
    if record.get("revokedAt"):
        return "REVOKED"
    if record.get("consumedAt"):
        return "CONSUMED"
    if datetime.fromisoformat(record["expiresAt"]) <= datetime.now(timezone.utc):
        return "EXPIRED"
    if record["version"] != request["version"]:
        return "WRONG_VERSION"
    if stage_awaiting(request["status"]) != record["stage"]:
        return "WRONG_STAGE"
    return None


def revoke_tokens(data: dict, request_id: str, reason: str) -> None:
    at = now_iso()
    for record in data["approvalTokens"]:
        if record["requestId"] == request_id and not record.get("consumedAt") and not record.get("revokedAt"):
            record["revokedAt"] = at
            record["revokedReason"] = reason


# --------------------------------------------------------------------- routing

def same_person(a: dict, b: dict) -> bool:
    if a.get("userId") and b.get("userId"):
        return a["userId"].lower() == b["userId"].lower()
    return (a.get("email") or "").strip().lower() == (b.get("email") or "").strip().lower()


def answerers(step: dict) -> list[dict]:
    return step.get("pool") or [step["approver"]]


def ciso_team_from_config(env: Mapping[str, str]) -> list[dict]:
    raw = (env.get("CISO_APPROVER_EMAILS") or "").strip()
    if not raw:
        email = (env.get("CISO_APPROVER_EMAIL") or "").strip().lower()
        if email:
            return [{"name": (env.get("CISO_APPROVER_NAME") or "").strip() or email, "email": email}]
        if (env.get("APP_ENV") or "").strip().lower() == "production":
            raise RoutingError("CISO_APPROVER_GROUP atau CISO_APPROVER_EMAILS belum diset. Pengajuan tidak dapat dikirim tanpa approver CISO.")
        return [{"name": "Bagus Nugroho", "email": "bagus.nugroho@example.com"}]
    team: list[dict] = []
    for entry in (part.strip() for part in raw.split(",")):
        if not entry:
            continue
        if "<" in entry and entry.endswith(">"):
            name, _, email = entry[:-1].partition("<")
            name, email = name.strip(), email.strip().lower()
        else:
            name, email = "", entry.lower()
        if "@" not in email or " " in email:
            raise RoutingError(f'CISO_APPROVER_EMAILS memuat alamat yang tidak sah: "{entry}".')
        if all(member["email"] != email for member in team):
            team.append({"name": name or email, "email": email})
    if not team:
        raise RoutingError("CISO_APPROVER_EMAILS diisi tetapi tidak memuat satu alamat pun.")
    return team


def resolve_ciso_team(env: Mapping[str, str], group_members: Callable[[str], list[dict]] | None) -> list[dict]:
    group = (env.get("CISO_APPROVER_GROUP") or "").strip()
    if group and group_members is not None:
        try:
            members = group_members(group)
        except Exception as error:  # the directory could not be read: nothing is sent
            raise RoutingError(f"Anggota tim CISO tidak bisa dibaca dari direktori ({group}): {error}. Pengajuan tidak dikirim.") from error
        team = [{"name": m.get("displayName") or m["mail"], "email": m["mail"].strip().lower()}
                for m in members if m.get("enabled") and (m.get("mail") or "").strip()]
        if not team:
            raise RoutingError(f"Group {group} tidak memiliki anggota aktif yang punya email. Pengajuan tidak dapat dirutekan ke tim CISO.")
    else:
        team = ciso_team_from_config(env)
    excluded = {e.strip().lower() for e in (env.get("CISO_EXCLUDE_EMAILS") or "").split(",") if e.strip()}
    eligible = [member for member in team if member["email"] not in excluded]
    if not eligible:
        raise RoutingError("Tidak ada anggota tim CISO yang bisa dikirimi permintaan persetujuan setelah pengecualian diterapkan.")
    return eligible


def resolve_manager(payload: dict, employee: dict | None) -> dict:
    if payload["kind"] == "ONBOARDING":
        name, email = payload["managerName"], payload["managerEmail"]
    elif payload["kind"] == "MOVEMENT":
        name, email = payload["toManagerName"], payload["toManagerEmail"]
    else:
        if employee is None:
            raise RoutingError("Karyawan yang diajukan tidak ditemukan, manager tidak dapat ditentukan.")
        if not employee.get("managerEmail"):
            raise RoutingError(f"{employee['displayName']} belum memiliki manager pada data direktori, sehingga pengajuan tidak dapat dirutekan.")
        name, email = employee.get("managerName", ""), employee["managerEmail"]
    address = (email or "").strip().lower()
    if not address:
        raise RoutingError("Alamat email approver kosong.")
    return {"name": (name or "").strip() or address, "email": address}


def separate_duties(requester: dict, manager: dict, team: list[dict]) -> list[dict]:
    if same_person(requester, manager):
        raise LifecycleError("INVALID", "Pemohon tidak boleh menjadi manager yang menyetujui pengajuannya sendiri.")
    eligible = [m for m in team if not same_person(requester, m) and not same_person(manager, m)]
    if not eligible:
        raise LifecycleError(
            "INVALID",
            "Pemohon tidak boleh menjadi approver CISO untuk pengajuannya sendiri."
            if any(same_person(requester, m) for m in team)
            else "Manager dan CISO harus dua orang berbeda. Pilih manager lain untuk pengajuan ini.",
        )
    return eligible


# --------------------------------------------------------------------- profile

def profile_of(employee: dict) -> dict:
    return {field: employee.get(field) for field in PROFILE_FIELD_LABELS}


def display_value(field: str, value) -> str:
    text = (value or "").strip() if isinstance(value, str) else (value or "")
    if not text:
        return "—"
    if field == "employmentType":
        return {"PERMANENT": "Permanent", "CONTRACT": "Temporary"}.get(text, text)
    if field == "locationType":
        return {"CABANG": "Cabang", "PUSAT": "Kantor pusat"}.get(text, text)
    return str(text)


def diff_profile(before: dict, after: dict) -> list[dict]:
    changes = []
    for field, label in PROFILE_FIELD_LABELS.items():
        old, new = (before.get(field) or ""), (after.get(field) or "")
        if field == "expiredDate" and after.get("employmentType") not in FIXED_TERM:
            new = ""
        if field == "branchName" and after.get("locationType") != "CABANG":
            new = ""
        if str(old).strip() != str(new).strip():
            changes.append({"field": field, "label": label, "from": display_value(field, old), "to": display_value(field, new)})
    return changes


def jakarta_text(iso: str) -> str:
    at = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(JAKARTA)
    months = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
    return f"{at.day} {months[at.month - 1]} {at.year}, {at:%H.%M}"


# --------------------------------------------------------------------- service

class Lifecycle:
    def __init__(self, store: Store, emitter, env: Mapping[str, str] | None = None,
                 group_members: Callable[[str], list[dict]] | None = None):
        self.store = store
        self.emitter = emitter  # outbox.Emitter: approval requests and results
        self.env = os.environ if env is None else env
        self.group_members = group_members

    # -- reading

    def list(self, kind: str | None = None, statuses: list[str] | None = None) -> list[dict]:
        requests = self.store.read()["lifecycleRequests"]
        matching = [r for r in requests if (not kind or r["type"] == kind) and (not statuses or r["status"] in statuses)]
        return sorted(matching, key=lambda r: r["createdAt"], reverse=True)

    def get(self, request_id: str) -> dict:
        found = next((r for r in self.store.read()["lifecycleRequests"] if r["id"] == request_id), None)
        if found is None:
            raise LifecycleError("NOT_FOUND", "Pengajuan tidak ditemukan.")
        return found

    def audit_trail(self, request_id: str) -> list[dict]:
        return [e for e in self.store.read()["auditEvents"] if e["correlationId"] == request_id]

    def jobs(self, request_id: str) -> list[dict]:
        return [j for j in self.store.read()["executionJobs"] if j["requestId"] == request_id]

    # -- helpers inside a write

    @staticmethod
    def _employee(data: dict, employee_id: str) -> dict:
        employee = next((e for e in data["employees"] if e["id"] == employee_id), None)
        if employee is None:
            raise LifecycleError("NOT_FOUND", "Karyawan yang diajukan tidak ditemukan.")
        return employee

    @staticmethod
    def _request(data: dict, request_id: str) -> dict:
        request = next((r for r in data["lifecycleRequests"] if r["id"] == request_id), None)
        if request is None:
            raise LifecycleError("NOT_FOUND", "Pengajuan tidak ditemukan.")
        return request

    @staticmethod
    def _no_active_request(data: dict, employee_id: str, except_id: str | None = None) -> None:
        clash = next((r for r in data["lifecycleRequests"]
                      if r.get("employeeId") == employee_id and r["id"] != except_id and is_active(r["status"])), None)
        if clash:
            raise LifecycleError("CONFLICT", f"Karyawan ini masih memiliki pengajuan aktif berstatus "
                                             f"{STATUS_LABELS.get(clash['status'], clash['status'])}. Selesaikan atau batalkan dulu.")

    @staticmethod
    def _with_directory_manager(data: dict, payload: dict) -> dict:
        if payload["kind"] not in ("ONBOARDING", "MOVEMENT"):
            return payload
        key = "managerEmail" if payload["kind"] == "ONBOARDING" else "toManagerEmail"
        address = payload[key].strip().lower()
        manager = next((e for e in data["employees"]
                        if e["email"].strip().lower() == address and e["status"] == "ACTIVE"), None)
        if manager is None:
            raise LifecycleError("INVALID", f"Manager harus karyawan aktif yang terdaftar di direktori. "
                                            f"{address or '(kosong)'} tidak ditemukan atau tidak aktif — pilih manager dari daftar.")
        name_key = "managerName" if payload["kind"] == "ONBOARDING" else "toManagerName"
        return {**payload, name_key: manager["displayName"], key: address}

    def _with_profile_changes(self, payload: dict, employee: dict | None) -> dict:
        if payload["kind"] != "PROFILE_UPDATE":
            return payload
        changes = diff_profile(profile_of(employee), payload["profile"])
        if not changes:
            raise LifecycleError("INVALID", "Tidak ada perubahan pada profil. Ubah minimal satu isian sebelum mengajukan.")
        return {**payload, "changes": changes}

    @staticmethod
    def _subject(payload: dict, employee: dict | None) -> dict:
        if payload["kind"] == "ONBOARDING":
            return {"displayName": payload["displayName"], "email": payload["email"], "department": payload["department"]}
        return {"displayName": employee["displayName"], "email": employee["email"], "department": employee["department"]}

    @staticmethod
    def _snapshot(employee: dict) -> dict:
        return {k: employee.get(k, "") for k in ("displayName", "email", "department", "jobTitle", "managerEmail", "status")}

    # -- writing

    def create_and_submit(self, payload: dict, effective_at: str | None, actor: dict) -> dict:
        """A new request, sent straight to its manager — the form's one button."""
        team = resolve_ciso_team(self.env, self.group_members)

        def write(data: dict) -> dict:
            employee_id = payload.get("employeeId")
            employee = self._employee(data, employee_id) if employee_id else None
            if employee_id:
                self._no_active_request(data, employee_id)
            built = self._with_directory_manager(data, self._with_profile_changes(payload, employee))
            at = now_iso()
            request = {
                "id": new_id("lr"), "type": built["kind"], "version": 1, "status": "DRAFT", "requester": actor,
                "employeeId": employee_id, "subject": self._subject(built, employee), "payload": built,
                "payloadHash": "", "approvals": [], "effectiveAt": effective_at, "policyVersion": POLICY_VERSION,
                "createdAt": at, "updatedAt": at,
            }
            data["lifecycleRequests"].append(request)
            audit(data, actor.get("userId") or actor["email"], actor["name"], "PORTAL", "request.drafted", request["id"],
                  {"type": request["type"], "version": 1})
            self._submit_in(data, request, actor, team)
            return request

        return self.store.mutate(write)

    def _submit_in(self, data: dict, request: dict, actor: dict, team: list[dict]) -> None:
        assert_transition(request["status"], "PENDING_MANAGER")
        employee = self._employee(data, request["employeeId"]) if request.get("employeeId") else None
        if request.get("employeeId"):
            self._no_active_request(data, request["employeeId"], request["id"])
        request["payload"] = self._with_directory_manager(data, self._with_profile_changes(request["payload"], employee))
        manager = resolve_manager(request["payload"], employee)
        eligible = separate_duties(request["requester"], manager, team)
        ciso_step = ({"stage": "CISO", "version": request["version"], "approver": eligible[0]} if len(eligible) == 1
                     else {"stage": "CISO", "version": request["version"],
                           "approver": {"name": f"Tim CISO · {len(eligible)} orang", "email": ""}, "pool": eligible})
        request["approvals"] = [{"stage": "MANAGER", "version": request["version"], "approver": manager}, ciso_step]
        request["payloadHash"] = hash_payload(request["payload"])
        request["beforeSnapshot"] = self._snapshot(employee) if employee else None
        request["status"] = "PENDING_MANAGER"
        request["submittedAt"] = request["updatedAt"] = now_iso()
        audit(data, actor.get("userId") or actor["email"], actor["name"], "PORTAL", "request.submitted", request["id"],
              {"version": request["version"], "manager": manager["email"], "ciso": ", ".join(m["email"] for m in eligible)})
        self.emitter.approval_request(data, request, "MANAGER")

    def decide_by_token(self, raw: str, decision: str) -> dict:
        if decision not in ("APPROVED", "REJECTED"):
            raise LifecycleError("INVALID", "Keputusan harus APPROVED atau REJECTED.")

        def write(data: dict) -> dict:
            record = _find_token(data["approvalTokens"], raw)
            if record is None:
                raise LifecycleError("NOT_FOUND", TOKEN_REJECTIONS["UNKNOWN"])
            request = self._request(data, record["requestId"])
            problem = _check_token(record, request)
            if problem:
                settled = self._settled(request, record)
                raise LifecycleError("CONFLICT", settled or TOKEN_REJECTIONS[problem])
            step = next((s for s in request["approvals"]
                         if s["stage"] == record["stage"] and s["version"] == request["version"]), None)
            if step is None:
                raise LifecycleError("CONFLICT", "Tahap persetujuan tidak ditemukan.")
            if step.get("decision"):
                raise LifecycleError("CONFLICT", self._settled(request, record) or TOKEN_REJECTIONS["CONSUMED"])
            member = next((m for m in answerers(step) if m["email"].strip().lower() == record["recipient"]), None)
            if member is None:
                raise LifecycleError("FORBIDDEN", "Tautan ini tidak ditujukan kepada approver tahap ini.")
            if hash_payload(request["payload"]) != request["payloadHash"]:
                raise LifecycleError("CONFLICT", "Isi pengajuan tidak cocok dengan sidik jari yang disetujui.")
            record["consumedAt"] = now_iso()
            self._apply_decision(data, request, step, decision, member)
            return request

        return self.store.mutate(write)

    def _apply_decision(self, data: dict, request: dict, step: dict, decision: str, actor: dict) -> None:
        at = now_iso()
        step.update(decision=decision, decidedAt=at, decidedBy=actor)
        to = "REJECTED" if decision == "REJECTED" else ("PENDING_CISO" if request["status"] == "PENDING_MANAGER" else "APPROVED")
        assert_transition(request["status"], to)
        request["status"], request["updatedAt"] = to, at
        if decision == "REJECTED":
            request["closedAt"] = at
        audit(data, actor.get("userId") or actor["email"], actor["name"], "EMAIL",
              "request.approved" if decision == "APPROVED" else "request.rejected", request["id"],
              {"stage": step["stage"], "version": request["version"], "status": request["status"]})
        if decision == "REJECTED":
            revoke_tokens(data, request["id"], "request-rejected")
            self.emitter.result(data, request, "REJECTED")
        elif request["status"] == "PENDING_CISO":
            self.emitter.approval_request(data, request, "CISO")
        if request["status"] == "APPROVED":
            effective = None if request["type"] == "ONBOARDING" else request.get("effectiveAt")
            queued = ("SCHEDULED" if effective and datetime.fromisoformat(effective) > datetime.now(timezone.utc)
                      else "QUEUED")
            assert_transition(request["status"], queued)
            request["status"] = queued
            audit(data, "system", "HC Portal", "SYSTEM", "request.scheduled" if queued == "SCHEDULED" else "request.queued",
                  request["id"], {"effectiveAt": effective or "segera"})
            revoke_tokens(data, request["id"], "request-approved")
            self.emitter.result(data, request, "APPROVED")

    def _settled(self, request: dict, record: dict) -> str | None:
        if request["version"] != record["version"]:
            return None
        step = next((s for s in request["approvals"]
                     if s["stage"] == record["stage"] and s["version"] == record["version"]), None)
        if not step or not step.get("decision") or not step.get("decidedBy"):
            return None
        verb = "disetujui" if step["decision"] == "APPROVED" else "ditolak"
        return f"Sudah {verb} oleh {step['decidedBy']['name']} pada {jakarta_text(step['decidedAt'])}. Tidak perlu tindakan dari Anda."

    def preview_by_token(self, raw: str) -> dict:
        """What the approval page shows: the request, or why the link is no use."""
        data = self.store.read()
        record = _find_token(data["approvalTokens"], raw)
        request = next((r for r in data["lifecycleRequests"] if record and r["id"] == record["requestId"]), None)
        if record is None or request is None:
            return {"ok": False, "reason": TOKEN_REJECTIONS["UNKNOWN"]}
        problem = _check_token(record, request)
        if problem:
            settled = self._settled(request, record)
            return {"ok": False, "reason": settled or TOKEN_REJECTIONS[problem], "settled": bool(settled)}
        step = next(s for s in request["approvals"] if s["stage"] == record["stage"] and s["version"] == request["version"])
        manager_step = next((s for s in request["approvals"] if s["stage"] == "MANAGER" and s["version"] == request["version"]), None)
        member = next((m for m in answerers(step) if m["email"].strip().lower() == record["recipient"]), step["approver"])
        return {
            "ok": True, "request": request, "stage": record["stage"], "approverName": member["name"],
            "teamSize": len(step["pool"]) if step.get("pool") and len(step["pool"]) > 1 else None,
            "managerDecision": ({"by": manager_step["decidedBy"]["name"], "at": jakarta_text(manager_step["decidedAt"])}
                                if record["stage"] == "CISO" and manager_step and manager_step.get("decidedBy") else None),
        }

    def cancel(self, request_id: str, actor: dict, is_admin: bool, reason: str | None = None) -> dict:
        def write(data: dict) -> dict:
            request = self._request(data, request_id)
            if not same_person(request["requester"], actor) and not is_admin:
                raise LifecycleError("FORBIDDEN", "Hanya pemohon yang dapat membatalkan pengajuan ini.")
            assert_transition(request["status"], "CANCELLED")
            revoke_tokens(data, request["id"], "request-cancelled")
            at = now_iso()
            request.update(status="CANCELLED", closedAt=at, updatedAt=at,
                           closedReason=(reason or "").strip() or "Dibatalkan oleh pemohon.")
            audit(data, actor.get("userId") or actor["email"], actor["name"], "PORTAL", "request.cancelled", request["id"],
                  {"reason": request["closedReason"]})
            return request

        return self.store.mutate(write)

    def retry(self, request_id: str, actor: dict, acknowledged: bool = False) -> dict:
        blocked = {
            "DRIFT": "Kondisi akun sudah berubah sejak disetujui, jadi mengulang akan menjalankan perubahan yang tidak lagi sesuai. Ajukan ulang agar kedua approver menilai keadaan sekarang.",
            "PAYLOAD_MISMATCH": "Isi pengajuan tidak cocok dengan yang disetujui. Pengajuan harus ditinjau dan diajukan ulang.",
        }

        def write(data: dict) -> dict:
            request = self._request(data, request_id)
            if request["status"] != "FAILED":
                raise LifecycleError("CONFLICT", f"Hanya pengajuan berstatus Gagal yang dapat dicoba lagi; ini berstatus {STATUS_LABELS[request['status']]}.")
            job = next((j for j in data["executionJobs"] if j["operationId"] == f"{request['id']}:{request['version']}"), None)
            if job and job.get("errorCode") in blocked:
                raise LifecycleError("CONFLICT", blocked[job["errorCode"]])
            if job and job.get("errorCode") == "LEASE_EXPIRED" and not acknowledged:
                raise LifecycleError("CONFLICT", "Worker sebelumnya berhenti tanpa melapor. Bandingkan objek direktori dengan checkpoint lebih dulu, lalu ulangi dengan konfirmasi rekonsiliasi.")
            assert_transition(request["status"], "QUEUED")
            at = now_iso()
            request.update(status="QUEUED", updatedAt=at)
            if job:
                for key in ("errorCode", "errorMessage", "finishedAt", "leaseWorkerId", "leaseExpiresAt"):
                    job.pop(key, None)
                job.update(state="CLAIMED", updatedAt=at)
            audit(data, actor.get("userId") or actor["email"], actor["name"], "PORTAL", "execution.retry_requested",
                  request["id"], {"reconciliationAcknowledged": acknowledged})
            return request

        return self.store.mutate(write)

    def revise_and_resubmit(self, request_id: str, payload: dict, effective_at: str | None, actor: dict) -> dict:
        team = resolve_ciso_team(self.env, self.group_members)

        def write(data: dict) -> dict:
            request = self._request(data, request_id)
            if not same_person(request["requester"], actor):
                raise LifecycleError("FORBIDDEN", "Hanya pemohon yang dapat merevisi pengajuan ini.")
            if payload["kind"] != request["type"]:
                raise LifecycleError("INVALID", "Revisi tidak boleh mengubah jenis pengajuan.")
            if payload.get("employeeId") != request.get("employeeId"):
                raise LifecycleError("INVALID", "Revisi tidak boleh mengganti karyawan yang diajukan. Batalkan pengajuan ini dan buat pengajuan baru.")
            employee = self._employee(data, payload["employeeId"]) if payload.get("employeeId") else None
            if request["status"] != "DRAFT":
                assert_transition(request["status"], "DRAFT")
                revoke_tokens(data, request["id"], "request-revised")
                request["version"] += 1
                request["status"] = "DRAFT"
            request["payload"] = payload
            request["subject"] = self._subject(payload, employee)
            request["effectiveAt"] = effective_at
            request["updatedAt"] = now_iso()
            audit(data, actor.get("userId") or actor["email"], actor["name"], "PORTAL", "request.revised", request["id"],
                  {"version": request["version"]})
            self._submit_in(data, request, actor, team)
            return request

        return self.store.mutate(write)
