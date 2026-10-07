"""Who may do what in the portal — the one place the matrix lives.

There is no approval permission: both approvals are given from email, through
a link issued to one approver's address, and no role here can approve.
Administering the portal is a different job from using it, so SYSTEM_ADMIN
cannot raise requests.
"""

ROLE_LABELS = {
    "HC_REQUESTER": "Human Capital",
    "SYSTEM_ADMIN": "Admin sistem",
    "OPS_OPERATOR": "Operator",
    "AUDITOR": "Auditor",
}

ROLE_PERMISSIONS = {
    "HC_REQUESTER": {"directory.read", "directory.export", "activity.read", "request.read", "request.create",
                     "request.cancel"},
    "SYSTEM_ADMIN": {"directory.read", "activity.read", "request.read", "execution.run"},
    "OPS_OPERATOR": {"directory.read", "activity.read", "request.read", "execution.run"},
    "AUDITOR": {"directory.read", "directory.export", "activity.read", "request.read"},
}


def has_permission(roles, permission: str) -> bool:
    return any(permission in ROLE_PERMISSIONS.get(role, set()) for role in roles)
