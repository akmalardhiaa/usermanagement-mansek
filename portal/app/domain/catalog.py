"""Divisions, job titles, access profiles, and where in AD each one lives.

The lists suggest; they do not constrain — a title nobody thought to add must
not block a legitimate hire. The OU and group DNs come from the environment
(the same AD_OU_* / AD_ACCESS_GROUP_* / AD_DEPARTMENT_* names as before);
the Java AD service reads the same values and refuses anything outside them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

DEPARTMENT_GROUPS: list[tuple[str, list[str]]] = [
    ("Front office", ["Investment Banking", "Institutional Sales", "Retail Brokerage", "Equity Research",
                      "Fixed Income", "Treasury", "Sales", "Research"]),
    ("Middle & back office", ["Risk Management", "Compliance", "Internal Audit", "Operations", "Settlement", "Legal"]),
    ("Korporat", ["Human Capital", "Finance", "Accounting", "Marketing", "Corporate Secretary", "General Affairs",
                  "Procurement", "Customer Care"]),
    ("Teknologi", ["IT — Engineering", "IT — Security", "IT — Infrastructure", "IT — Data", "IT — Product",
                   "IT — Support", "Engineering", "Cyber Security", "Analytics"]),
]

JOB_TITLE_GROUPS: list[tuple[str, list[str]]] = [
    ("Engineering", ["Backend Engineer", "Senior Backend Engineer", "Frontend Engineer", "Senior Frontend Engineer",
                     "Fullstack Engineer", "Mobile Engineer", "DevOps Engineer", "Site Reliability Engineer",
                     "QA Engineer", "Senior QA Engineer", "Engineering Manager"]),
    ("Data, produk, desain", ["Data Analyst", "Data Engineer", "Data Scientist", "Product Manager",
                              "Product Designer", "UX Researcher", "Business Analyst"]),
    ("Keamanan", ["Security Analyst", "Security Engineer", "IT Security Officer"]),
    ("Keuangan & pasar modal", ["Analyst", "Financial Analyst", "Equity Analyst", "Investment Analyst",
                                "Research Analyst", "Fixed Income Analyst", "Relationship Manager",
                                "Account Officer", "Accounting Staff", "Treasury Officer"]),
    ("Risiko, kepatuhan, hukum", ["Compliance Officer", "Risk Officer", "Auditor Internal", "Legal Officer"]),
    ("Human Capital & korporat", ["HC Officer", "Recruiter", "Head of Human Capital", "Corporate Secretary Officer",
                                  "General Affairs Officer", "Customer Care Officer"]),
    ("Kepemimpinan", ["Supervisor", "Assistant Manager", "Manager", "Senior Manager", "Head of Division", "Director"]),
]

DEPARTMENTS = [item for _, items in DEPARTMENT_GROUPS for item in items]
JOB_TITLES = [item for _, items in JOB_TITLE_GROUPS for item in items]

EMPLOYMENT_TYPES = {"PERMANENT": "Tetap (Permanent)", "CONTRACT": "Kontrak (Temporary)"}
FIXED_TERM = {"CONTRACT"}
LOCATIONS = {"PUSAT": "Kantor pusat", "CABANG": "Cabang"}
TERMINATION_REASONS = {
    "RESIGN": "Mengundurkan diri",
    "CONTRACT_END": "Kontrak berakhir",
    "RETIREMENT": "Pensiun",
    "TERMINATION": "Pemutusan hubungan kerja",
    "OTHER": "Lainnya",
}


@dataclass(frozen=True)
class AccessProfile:
    id: str
    label: str
    description: str


ACCESS_PROFILES = [
    AccessProfile("standard", "Standar karyawan", "Akses dasar: email, direktori, dan aplikasi umum perusahaan."),
    AccessProfile("engineering", "Engineering", "Akses standar ditambah repository kode dan lingkungan pengembangan."),
    AccessProfile("finance", "Finance", "Akses standar ditambah aplikasi keuangan dan pelaporan."),
    AccessProfile("security", "IT Security", "Akses standar ditambah perkakas keamanan dan pemantauan."),
    AccessProfile("none", "Tanpa akses aplikasi", "Hanya akun direktori. Dipakai saat akses aplikasi diajukan terpisah."),
]
PROFILE_IDS = {profile.id for profile in ACCESS_PROFILES}

_PROFILE_DIRECTORY = {
    "standard": (["AD_ACCESS_GROUP_BASE"], "AD_OU_STANDARD"),
    "engineering": (["AD_ACCESS_GROUP_BASE", "AD_ACCESS_GROUP_ENGINEERING"], "AD_OU_ENGINEERING"),
    "finance": (["AD_ACCESS_GROUP_BASE", "AD_ACCESS_GROUP_FINANCE"], "AD_OU_FINANCE"),
    "security": (["AD_ACCESS_GROUP_BASE", "AD_ACCESS_GROUP_SECURITY"], "AD_OU_SECURITY"),
    "none": ([], "AD_OU_STANDARD"),
}


class CatalogError(Exception):
    """A DN the plan needs is not configured."""


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _required(env: Mapping[str, str], name: str) -> str:
    value = (env.get(name) or "").strip()
    if not value:
        raise CatalogError(f"{name} wajib diisi untuk menjalankan perubahan ke Active Directory.")
    return value


def profile_groups(profile_id: str, env: Mapping[str, str] | None = None) -> list[str]:
    env = _env(env)
    names, _ = _PROFILE_DIRECTORY.get(profile_id, _PROFILE_DIRECTORY["standard"])
    return [_required(env, name) for name in names]


def all_catalogue_groups(env: Mapping[str, str] | None = None) -> list[str]:
    env = _env(env)
    names = ["AD_ACCESS_GROUP_BASE", "AD_ACCESS_GROUP_ENGINEERING", "AD_ACCESS_GROUP_FINANCE", "AD_ACCESS_GROUP_SECURITY"]
    seen: list[str] = []
    for name in names:
        value = (env.get(name) or "").strip()
        if value and value.lower() not in {s.lower() for s in seen}:
            seen.append(value)
    return seen


def profile_ou(profile_id: str, env: Mapping[str, str] | None = None) -> str:
    env = _env(env)
    _, name = _PROFILE_DIRECTORY.get(profile_id, _PROFILE_DIRECTORY["standard"])
    return _required(env, name)


def quarantine_ou(env: Mapping[str, str] | None = None) -> str:
    return _required(_env(env), "AD_QUARANTINE_OU")


def escape_dn_value(value: str) -> str:
    """RFC 4514: a division name with a comma or a plus cannot change the DN's shape."""
    escaped = "".join("\\" + char if char in ',+"\\<>;=' else char for char in value)
    if escaped.startswith(("#", " ")):
        escaped = "\\" + escaped
    if escaped.endswith(" "):
        escaped = escaped[:-1] + "\\ "
    return escaped


def department_pairs(env: Mapping[str, str] | None = None) -> list[tuple[str, str]]:
    raw = (_env(env).get("AD_DEPARTMENT_OUS") or "").strip()
    pairs: list[tuple[str, str]] = []
    for entry in (part.strip() for part in raw.split(";")):
        if not entry:
            continue
        department, arrow, ou = entry.partition("=>")
        if not arrow or not department.strip() or not ou.strip():
            raise CatalogError(f'AD_DEPARTMENT_OUS: "{entry}" bukan bentuk Divisi=>DN OU. Pisahkan tiap divisi dengan ;')
        pairs.append((department.strip(), ou.strip()))
    return pairs


def department_parent(env: Mapping[str, str] | None = None) -> str | None:
    return (_env(env).get("AD_DEPARTMENT_OU_PARENT") or "").strip() or None


def department_ou(department: str | None, env: Mapping[str, str] | None = None) -> str | None:
    """The division's OU: the explicit pair first, then the parent convention."""
    name = (department or "").strip()
    if not name:
        return None
    for pair_department, ou in department_pairs(env):
        if pair_department.lower() == name.lower():
            return ou
    parent = department_parent(env)
    return f"OU={escape_dn_value(name)},{parent}" if parent else None


def same_dn(left: str | None, right: str | None) -> bool:
    def norm(dn: str) -> str:
        return ",".join(part.strip().lower() for part in dn.split(","))

    return bool(left) and bool(right) and norm(left) == norm(right)
