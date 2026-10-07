"""What an approved request does to Active Directory, step by step.

Each step is idempotent at the directory (the Java service makes repeats
harmless), so a run that stopped half-way resumes from the first step not yet
done. The postconditions are read back from AD before anything is marked
complete.
"""

from __future__ import annotations

import re
from typing import Mapping

from app.domain.catalog import (all_catalogue_groups, department_ou, profile_groups, profile_ou, quarantine_ou,
                                same_dn)


def account_name_for(email: str) -> str:
    return re.sub(r"[^a-z0-9._-]", "", email.split("@")[0].lower())


def build_plan(payload: dict, before: dict | None, env: Mapping[str, str] | None = None) -> dict:
    kind = payload["kind"]
    if kind == "PROFILE_UPDATE":
        attributes = {"displayName": payload["profile"]["displayName"], "department": payload["profile"]["department"],
                      "title": payload["profile"]["jobTitle"]}
        return {
            "steps": [
                {"key": "set-attributes", "label": "Memperbarui nama, departemen, dan jabatan", "params": attributes},
                {"key": "verify", "label": "Membaca ulang hasil dari direktori", "params": {}},
            ],
            "post": {"enabled": (before or {}).get("enabled", True), "ou": (before or {}).get("ou"),
                     "required": list((before or {}).get("groups", [])), "forbidden": [], "attributes": attributes},
        }

    if kind == "ONBOARDING":
        profile = payload.get("accessProfileId") or "standard"
        groups = profile_groups(profile, env)
        ou = (profile_ou(payload["accessProfileId"], env) if payload.get("accessProfileId")
              else department_ou(payload["department"], env) or profile_ou("standard", env))
        attributes = {"department": payload["department"], "title": payload["jobTitle"],
                      "manager": account_name_for(payload["managerEmail"])}
        return {
            "steps": [
                {"key": "create-account", "label": "Membuat objek akun dalam keadaan nonaktif", "params": {
                    "sAMAccountName": payload.get("userId") or account_name_for(payload["email"]),
                    "displayName": payload["displayName"], "givenName": payload["firstName"], "sn": payload["lastName"],
                    "mail": payload["email"], "ou": ou}},
                {"key": "set-attributes", "label": "Mengisi atribut identitas dan manager", "params": attributes},
                {"key": "grant-groups", "label": "Menambahkan keanggotaan group sesuai profil akses", "params": {"groups": groups}},
                {"key": "verify", "label": "Membaca ulang hasil dari direktori", "params": {}},
                {"key": "enable-account", "label": "Mengaktifkan akun setelah seluruh langkah wajib selesai", "params": {}},
            ],
            "post": {"enabled": True, "ou": ou, "required": groups, "forbidden": [], "attributes": attributes},
        }

    if kind == "MOVEMENT":
        attributes = {"department": payload["toDepartment"], "title": payload["toJobTitle"],
                      "manager": account_name_for(payload["toManagerEmail"])}
        if not payload.get("accessProfileId"):
            division = department_ou(payload["toDepartment"], env)
            move_to = division if division and not same_dn((before or {}).get("ou"), division) else None
            steps = [{"key": "set-attributes", "label": "Memperbarui divisi, jabatan, dan manager", "params": attributes}]
            if move_to:
                steps.append({"key": "move-ou", "label": "Memindahkan objek ke OU divisi baru", "params": {"ou": move_to}})
            steps.append({"key": "verify", "label": "Membaca ulang hasil dari direktori", "params": {}})
            return {"steps": steps, "post": {"enabled": (before or {}).get("enabled", True),
                                             "ou": move_to or (before or {}).get("ou"),
                                             "required": list((before or {}).get("groups", [])), "forbidden": [],
                                             "attributes": attributes}}
        next_groups = profile_groups(payload["accessProfileId"], env)
        ou = profile_ou(payload["accessProfileId"], env)
        managed = all_catalogue_groups(env)
        revoke = [g for g in (before or {}).get("groups", [])
                  if any(same_dn(g, m) for m in managed) and not any(same_dn(g, n) for n in next_groups)]
        return {
            "steps": [
                {"key": "set-attributes", "label": "Memperbarui divisi, jabatan, dan manager", "params": attributes},
                {"key": "revoke-groups", "label": "Mencabut akses lama yang tidak lagi berlaku", "params": {"groups": revoke}},
                {"key": "grant-groups", "label": "Menambahkan akses sesuai profil baru", "params": {"groups": next_groups}},
                {"key": "move-ou", "label": "Memindahkan objek ke OU tujuan", "params": {"ou": ou}},
                {"key": "verify", "label": "Membaca ulang hasil dari direktori", "params": {}},
            ],
            "post": {"enabled": True, "ou": ou, "required": next_groups, "forbidden": revoke, "attributes": attributes},
        }

    # TERMINATION
    ou = quarantine_ou(env)
    managed = all_catalogue_groups(env)
    revoke = [g for g in (before or {}).get("groups", []) if any(same_dn(g, m) for m in managed)]
    return {
        "steps": [
            {"key": "disable-account", "label": "Menonaktifkan akun — tindakan pertama", "params": {}},
            {"key": "revoke-groups", "label": "Mencabut keanggotaan group dalam lingkup kebijakan", "params": {"groups": revoke}},
            {"key": "move-ou", "label": "Memindahkan objek ke OU karantina", "params": {"ou": ou}},
            {"key": "verify", "label": "Membaca ulang hasil dari direktori", "params": {}},
        ],
        "post": {"enabled": False, "ou": ou, "required": [], "forbidden": revoke, "attributes": {}},
    }


def verify(plan: dict, state: dict | None) -> str | None:
    """Why the directory does not look as the plan promised, or None."""
    if not state:
        return "Objek tidak ditemukan saat pembacaan ulang."
    post = plan["post"]
    if state["enabled"] != post["enabled"]:
        return f"enabled seharusnya {post['enabled']}, terbaca {state['enabled']}."
    if post.get("ou") and not same_dn(state.get("ou"), post["ou"]):
        return f"OU seharusnya {post['ou']}, terbaca {state.get('ou')}."
    held = state.get("groups", [])
    for group in post["required"]:
        if not any(same_dn(group, h) for h in held):
            return f"Group wajib belum ada: {group}."
    for group in post["forbidden"]:
        if any(same_dn(group, h) for h in held):
            return f"Group seharusnya dicabut masih ada: {group}."
    attributes = post["attributes"]
    for key, field in (("displayName", "displayName"), ("department", "department"), ("title", "title")):
        if attributes.get(key) and state.get(field) != attributes[key]:
            return f"{key} seharusnya {attributes[key]}, terbaca {state.get(field)}."
    return None
