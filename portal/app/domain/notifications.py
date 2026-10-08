"""What the bell in the header lists: requests that need somebody, and recent outcomes.

  FAILED            stopped before finishing — somebody has to look
  WAITING_TOO_LONG  a manager or the CISO team has not answered for a day
  REJECTED          turned down in the last three days
  COMPLETED         carried out in AD in the last two days

Only the first two count on the bell: an outcome is news, not work.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.domain.lifecycle import stage_awaiting

WAITING_LATE_AFTER_HOURS = 24
RECENT_HOURS = {"REJECTED": 72, "COMPLETED": 48}
ORDER = {"FAILED": 0, "WAITING_TOO_LONG": 1, "REJECTED": 2, "COMPLETED": 3}
NEEDS_ATTENTION = {"FAILED", "WAITING_TOO_LONG"}
MAX_ITEMS = 12


def _hours_since(iso: str, now: datetime) -> float | None:
    try:
        then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (now - then).total_seconds() / 3600


def _classify(request: dict, now: datetime) -> dict | None:
    base = {"id": request["id"], "type": request["type"], "subjectName": request["subject"]["displayName"],
            "department": request["subject"].get("department") or "", "at": request["updatedAt"]}
    if request["status"] == "FAILED":
        return {**base, "kind": "FAILED"}
    awaiting = stage_awaiting(request["status"])
    hours = _hours_since(request["updatedAt"], now)
    if awaiting:
        if hours is None or hours < WAITING_LATE_AFTER_HOURS:
            return None
        return {**base, "kind": "WAITING_TOO_LONG", "stage": awaiting, "waitingHours": int(hours)}
    if request["status"] in RECENT_HOURS:
        if hours is None or hours > RECENT_HOURS[request["status"]]:
            return None
        return {**base, "kind": request["status"]}
    return None


def notifications_for(requests: list[dict], now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    items = [item for item in (_classify(r, now) for r in requests) if item]
    # Newest first within a kind; the second sort is stable and keeps that order.
    items.sort(key=lambda item: item["at"], reverse=True)
    items.sort(key=lambda item: ORDER[item["kind"]])
    return {"items": items[:MAX_ITEMS], "total": len(items),
            "needsAttention": sum(1 for item in items if item["kind"] in NEEDS_ATTENTION)}


def waited_text(hours: int) -> str:
    return f"{hours // 24} hari" if hours >= 24 else f"{hours} jam"
