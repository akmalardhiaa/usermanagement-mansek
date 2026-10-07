"""Reset two-step verification for one person, for a lost or replaced phone.

    python scripts/mfa_reset.py <username>      remove that person's authenticator
    python scripts/mfa_reset.py --list          who has one, and since when

On the office server, inside the container:

    podman exec hc-portal-v2 python scripts/mfa_reset.py <username>

The person connects a new phone at their next sign-in. Check it really is them
first: whoever signs in next with their password connects the phone. Never
reads or prints a secret.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _path(name: str, default: str) -> Path:
    configured = os.environ.get(name, "").strip()
    return Path(configured) if configured else Path(os.environ.get("HC_DATA_DIR", "data")) / default


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Pakai: python scripts/mfa_reset.py <username>   atau   --list")
        return 2

    mfa_file = _path("HC_MFA_FILE", "hc-mfa.json")
    log_file = _path("HC_SECURITY_LOG_FILE", "hc-security-log.jsonl")
    try:
        data = json.loads(mfa_file.read_text(encoding="utf-8").lstrip("﻿"))
    except FileNotFoundError:
        data = {"enrollments": []}

    if argv[1] == "--list":
        if not data["enrollments"]:
            print("Belum ada yang mendaftarkan authenticator.")
        for entry in data["enrollments"]:
            print(f"{entry['userId']}  terdaftar sejak {entry['enrolledAt']}")
        return 0

    user = argv[1].strip().split("\\")[-1].split("@")[0].lower()
    remaining = [entry for entry in data["enrollments"] if entry["userId"] != user]
    if len(remaining) == len(data["enrollments"]):
        print(f"{user} tidak punya authenticator terdaftar. Tidak ada yang diubah.")
        return 0

    tmp = mfa_file.with_name(f"{mfa_file.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps({"enrollments": remaining}, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, mfa_file)

    event = {"at": datetime.now(timezone.utc).isoformat(), "type": "mfa.reset", "username": user,
             "detail": "direset lewat scripts/mfa_reset.py"}
    try:
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")
    except OSError:
        pass
    print(f"2FA milik {user} direset. Saat login berikutnya dia memindai kode QR baru dengan HP-nya.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
