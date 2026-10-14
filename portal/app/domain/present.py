"""A request's content as rows of label and value — for pages and emails alike,
so an approver reads in the email exactly what HC sees in the portal."""

from __future__ import annotations

from app.domain.catalog import EMPLOYMENT_TYPES, LOCATIONS, TERMINATION_REASONS, WORKER_CATEGORIES


def _date(value: str | None) -> str:
    if not value:
        return "—"
    months = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September",
              "Oktober", "November", "Desember"]
    year, month, day = value[:10].split("-")
    return f"{int(day)} {months[int(month) - 1]} {year}"


def payload_rows(payload: dict, internal: bool = False) -> list[tuple[str, str]]:
    kind = payload.get("kind")
    rows: list[tuple[str, str]] = []
    if kind == "ONBOARDING":
        rows = [
            ("Nama lengkap", payload["displayName"]),
            ("Email", payload["email"]),
            ("User ID", payload.get("userId") or "—"),
            ("Jabatan", payload["jobTitle"]),
            ("Departemen", payload["department"]),
            ("Jenis pekerja", WORKER_CATEGORIES.get(payload.get("workerCategory", ""), "—")),
            ("Status kepegawaian", EMPLOYMENT_TYPES.get(payload.get("employmentType", ""), "—")),
        ]
        if payload.get("expiredDate"):
            rows.append(("Tanggal terakhir bekerja", _date(payload["expiredDate"])))
        location = LOCATIONS.get(payload.get("locationType", ""), "—")
        if payload.get("branchName"):
            location = f"{location} — {payload['branchName']}"
        rows += [
            ("Lokasi", location),
            ("Manager", f"{payload['managerName']} ({payload['managerEmail']})"),
        ]
        if payload.get("jobDescription"):
            rows.append(("Keterangan jabatan", payload["jobDescription"]))
    elif kind == "MOVEMENT":
        rows = [
            ("Departemen tujuan", payload["toDepartment"]),
            ("Jabatan tujuan", payload["toJobTitle"]),
            ("Jenis pekerja tujuan", WORKER_CATEGORIES.get(payload.get("toWorkerCategory", ""), "—")),
            ("Lokasi tujuan", LOCATIONS.get(payload.get("toLocationType", ""), "—")
             + (f" — {payload['toBranchName']}" if payload.get("toBranchName") else "")),
            ("Manager tujuan", f"{payload['toManagerName']} ({payload['toManagerEmail']})"),
        ]
        if payload.get("reason"):
            rows.append(("Alasan", payload["reason"]))
    elif kind == "TERMINATION":
        rows = [
            ("Tanggal terakhir bekerja", _date(payload["lastWorkingDate"])),
            ("Kategori alasan", TERMINATION_REASONS.get(payload.get("reasonCategory") or "", "—")),
            ("Serah terima kepada", payload.get("handoverTo") or "—"),
        ]
        if internal and payload.get("note"):
            rows.append(("Catatan (internal)", payload["note"]))
    elif kind == "PROFILE_UPDATE":
        rows = [(change["label"], f"{change['from']} → {change['to']}") for change in payload.get("changes", [])]
    return rows


def date_text(value: str | None) -> str:
    return _date(value)
