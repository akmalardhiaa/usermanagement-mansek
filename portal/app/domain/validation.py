"""Form input for the four kinds of request, checked before anything is stored.

Every message names the field the way the form does, so HC can fix it without
asking anyone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Mapping

from app.domain.catalog import EMPLOYMENT_TYPES, FIXED_TERM, PROFILE_IDS, TERMINATION_REASONS

JAKARTA = timezone(timedelta(hours=7))  # WIB has no daylight saving
EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
USER_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,19}$")

# How far back and forward each date may plausibly be, in days.
BOUNDS = {
    "effectiveAt": (30, 365),
    "startDate": (365, 365),
    "lastWorkingDate": (365, 365),
    "newContractEnd": (30, 3650),
    "contractEnd": (3650, 3650),
}

TYPES = ("ONBOARDING", "MOVEMENT", "TERMINATION", "PROFILE_UPDATE")


def jakarta_today(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(JAKARTA).date()


def next_day(value: str) -> str:
    return (date.fromisoformat(value[:10]) + timedelta(days=1)).isoformat()


def start_of_day(value: str) -> str:
    return f"{value}T00:00:00+07:00" if len(value) == 10 else value


def date_bounds(kind: str, now: datetime | None = None) -> tuple[str, str]:
    past, future = BOUNDS[kind]
    today = jakarta_today(now)
    return (today - timedelta(days=past)).isoformat(), (today + timedelta(days=future)).isoformat()


@dataclass
class Parsed:
    payload: dict | None
    effective_at: str | None
    errors: dict[str, str]

    @property
    def ok(self) -> bool:
        return not self.errors and self.payload is not None


class _Form:
    def __init__(self, data: Mapping[str, str], now: datetime | None):
        self.data = data
        self.errors: dict[str, str] = {}
        self.now = now

    def text(self, field: str) -> str:
        return (self.data.get(field) or "").strip()

    def required(self, field: str, label: str, minimum: int, maximum: int) -> str:
        value = self.text(field)
        if not value:
            self.errors[field] = f"{label} wajib diisi."
        elif len(value) < minimum:
            self.errors[field] = f"{label} minimal {minimum} karakter."
        elif len(value) > maximum:
            self.errors[field] = f"{label} maksimal {maximum} karakter."
        return value

    def optional(self, field: str, label: str, maximum: int) -> str | None:
        value = self.text(field)
        if len(value) > maximum:
            self.errors[field] = f"{label} maksimal {maximum} karakter."
        return value or None

    def email(self, field: str, label: str) -> str:
        value = self.required(field, label, 5, 200).lower()
        if value and field not in self.errors and not EMAIL.match(value):
            self.errors[field] = f"{label} tidak valid."
        return value

    def date(self, field: str, label: str, bounds: str, required: bool) -> str | None:
        value = self.text(field)
        if not value:
            if required:
                self.errors[field] = f"{label} wajib diisi."
            return None
        try:
            parsed = date.fromisoformat(value[:10])
        except ValueError:
            self.errors[field] = f"{label} tidak valid: gunakan tanggal yang ada di kalender, dengan tahun 4 digit."
            return None
        earliest, latest = date_bounds(bounds, self.now)
        if parsed.isoformat() < earliest:
            self.errors[field] = f"{label} terlalu jauh ke belakang — paling awal {earliest}."
            return None
        if parsed.isoformat() > latest:
            self.errors[field] = f"{label} terlalu jauh ke depan — paling lambat {latest}. Periksa angka tahunnya."
            return None
        return parsed.isoformat()

    def choice(self, field: str, label: str, allowed, required: bool = True) -> str | None:
        value = self.text(field).upper() if field != "accessProfileId" else self.text(field)
        if not value:
            if required:
                self.errors[field] = f"{label} wajib dipilih."
            return None
        if value not in allowed:
            self.errors[field] = f"{label} tidak dikenal."
            return None
        return value


def _employment(form: _Form, prefix: str = "") -> tuple[str | None, str | None, str | None, str | None]:
    employment = form.choice(f"{prefix}employmentType", "Status kepegawaian", EMPLOYMENT_TYPES)
    expired = (
        form.date(f"{prefix}expiredDate", "Tanggal terakhir bekerja", "newContractEnd", True)
        if employment in FIXED_TERM
        else None
    )
    location = form.choice(f"{prefix}locationType", "Lokasi", {"PUSAT", "CABANG"})
    branch = form.required(f"{prefix}branchName", "Nama cabang", 2, 120) if location == "CABANG" else None
    return employment, expired, location, branch


def parse_request(kind: str, data: Mapping[str, str], now: datetime | None = None) -> Parsed:
    form = _Form(data, now)
    kind = kind.upper()
    if kind not in TYPES:
        return Parsed(None, None, {"type": "Jenis pengajuan harus Onboarding, Movement, Termination, atau Perubahan Profil."})

    payload: dict | None = None
    effective_at = None if kind == "ONBOARDING" else form.date("effectiveAt", "Waktu efektif", "effectiveAt", False)

    if kind == "ONBOARDING":
        first = form.required("firstName", "Nama depan", 1, 80)
        last = form.required("lastName", "Nama belakang", 1, 80)
        display = form.required("displayName", "Nama lengkap", 2, 160)
        address = form.email("email", "Email")
        user_id = form.text("userId").lower()
        if not user_id:
            form.errors["userId"] = "User ID wajib diisi."
        elif not USER_ID.match(user_id):
            form.errors["userId"] = ("User ID hanya boleh huruf kecil, angka, titik, garis bawah, dan tanda hubung, "
                                     "2-20 karakter, diawali huruf atau angka.")
        title = form.required("jobTitle", "Jabatan", 2, 120)
        department = form.required("department", "Departemen", 2, 120)
        manager_name = form.required("managerName", "Nama manager", 2, 120)
        manager_email = form.email("managerEmail", "Manager")
        start = form.date("startDate", "Tanggal mulai", "startDate", False) or jakarta_today(now).isoformat()
        profile = form.choice("accessProfileId", "Profil akses", PROFILE_IDS, required=False)
        description = form.optional("jobDescription", "Keterangan jabatan", 2000)
        employment, expired, location, branch = _employment(form)
        if not form.errors:
            payload = {
                "kind": "ONBOARDING", "firstName": first, "lastName": last, "displayName": display,
                "email": address, "userId": user_id, "jobTitle": title, "jobDescription": description,
                "department": department, "employmentType": employment, "expiredDate": expired,
                "locationType": location, "branchName": branch, "managerName": manager_name,
                "managerEmail": manager_email, "startDate": start, "accessProfileId": profile,
            }

    elif kind == "MOVEMENT":
        employee_id = form.required("employeeId", "Karyawan", 1, 120)
        department = form.required("toDepartment", "Departemen tujuan", 2, 120)
        title = form.required("toJobTitle", "Jabatan tujuan", 2, 120)
        manager_name = form.required("toManagerName", "Nama manager tujuan", 2, 120)
        manager_email = form.email("toManagerEmail", "Manager tujuan")
        profile = form.choice("accessProfileId", "Profil akses", PROFILE_IDS, required=False)
        reason = form.optional("reason", "Alasan pemindahan", 2000)
        description = form.optional("toJobDescription", "Keterangan jabatan", 2000)
        if not form.errors:
            payload = {
                "kind": "MOVEMENT", "employeeId": employee_id, "toDepartment": department, "toJobTitle": title,
                "toJobDescription": description, "toManagerName": manager_name, "toManagerEmail": manager_email,
                "accessProfileId": profile, "reason": reason,
            }

    elif kind == "TERMINATION":
        employee_id = form.required("employeeId", "Karyawan", 1, 120)
        last_day = form.date("lastWorkingDate", "Tanggal terakhir bekerja", "lastWorkingDate", True)
        reason = form.choice("reasonCategory", "Kategori alasan", TERMINATION_REASONS, required=False)
        handover = form.optional("handoverTo", "Serah terima kepada", 160)
        note = form.optional("note", "Catatan", 2000)  # internal: never travels into an email
        if not form.errors:
            payload = {
                "kind": "TERMINATION", "employeeId": employee_id, "reasonCategory": reason,
                "lastWorkingDate": last_day, "handoverTo": handover, "note": note,
            }
            # One date, not two: the account goes the morning after the last working day.
            effective_at = effective_at or next_day(last_day)

    else:  # PROFILE_UPDATE
        employee_id = form.required("employeeId", "Karyawan", 1, 120)
        first = form.required("firstName", "Nama depan", 1, 80)
        last = form.required("lastName", "Nama belakang", 1, 80)
        display = form.required("displayName", "Nama lengkap", 2, 160)
        title = form.required("jobTitle", "Jabatan", 2, 120)
        department = form.required("department", "Departemen", 2, 120)
        description = form.optional("jobDescription", "Keterangan jabatan", 2000)
        hc_note = form.optional("description", "Catatan HC", 2000)
        employment, expired, location, branch = _employment(form)
        if not form.errors:
            payload = {
                "kind": "PROFILE_UPDATE", "employeeId": employee_id,
                "profile": {
                    "firstName": first, "lastName": last, "displayName": display, "jobTitle": title,
                    "jobDescription": description, "department": department, "employmentType": employment,
                    "expiredDate": expired, "locationType": location, "branchName": branch, "description": hc_note,
                },
                "changes": [],  # computed by the server against the record, never taken from the form
            }

    if form.errors or payload is None:
        return Parsed(None, None, form.errors)
    return Parsed({k: v for k, v in payload.items() if v is not None}, start_of_day(effective_at) if effective_at else None, {})
