"""Asking AD, every few minutes, whether the people signed in still should be.

A session used to last its full eight hours whatever happened in the
directory. Now each signed-in account is read with the service account, and
its sessions end when the account is gone, disabled, or its portal roles
changed. When AD cannot be read nothing is revoked — throwing everybody out
because a domain controller restarted would teach people to resent the check.
"""

from __future__ import annotations

from app.ad_client import AdClient, AdError
from app.security.security_log import SecurityLog
from app.security.sessions import SessionStore


def recheck_sessions(sessions: SessionStore, ad: AdClient, security: SecurityLog) -> dict:
    users = sessions.live_users()
    report = {"checked": len(users), "revoked": [], "error": None}
    verdicts = []
    try:
        for user_id, user in users.items():
            standing = ad.standing(user_id)
            if not standing["found"]:
                verdicts.append((user_id, user["username"], "akun tidak ada lagi di AD"))
            elif not standing["enabled"]:
                verdicts.append((user_id, user["username"], "akun dinonaktifkan di AD"))
            elif sorted(standing["roles"]) != sorted(user["roles"]):
                verdicts.append((user_id, user["username"], "keanggotaan group portal berubah"))
    except AdError as error:
        # Nothing on a partial read: the outcome must not depend on where AD stopped answering.
        report["error"] = str(error)
        return report
    for user_id, username, reason in verdicts:
        if sessions.revoke_user(user_id, f"ad-recheck: {reason}"):
            report["revoked"].append({"username": username, "reason": reason})
            security.record("session.revoked", username, detail=reason)
    return report
