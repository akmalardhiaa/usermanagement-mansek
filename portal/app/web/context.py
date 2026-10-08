"""What every page handler needs: the services, the session, and rendering."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.ad_client import AdClient
from app.domain.lifecycle import STATUS_LABELS, TYPE_LABELS, Lifecycle
from app.domain.notifications import notifications_for, waited_text
from app.domain.outbox import Dispatcher
from app.domain.present import date_text
from app.domain.store import Store
from app.domain.worker import Worker
from app.security.mfa_store import MfaStore
from app.security.pending import PendingLogins
from app.security.security_log import SecurityLog
from app.security.sessions import SESSION_COOKIE, Session, SessionStore
from app.security.throttle import Throttle
from app.settings import Settings
from app.web.roles import ROLE_LABELS, has_permission

APP_DIR = Path(__file__).resolve().parents[1]
TEMPLATES = Jinja2Templates(directory=APP_DIR / "templates")
TEMPLATES.env.globals.update(STATUS_LABELS=STATUS_LABELS, TYPE_LABELS=TYPE_LABELS, ROLE_LABELS=ROLE_LABELS,
                             date_text=date_text, waited_text=waited_text)

THEME_COOKIE = "hc_theme"
THEMES = ("light", "dark")


def _datetime_text(iso: str | None) -> str:
    if not iso:
        return "—"
    from app.domain.lifecycle import jakarta_text

    return f"{jakarta_text(iso)} WIB"


def initials(name: str | None) -> str:
    parts = [part for part in (name or "").split() if part]
    return "".join(part[0].upper() for part in parts[:2]) or "?"


def is_new(created_at: str | None, hours: int = 24) -> bool:
    """Created in the last day — flagged in the table so a new hire is easy to spot."""
    try:
        created = datetime.fromisoformat((created_at or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    age = datetime.now(timezone.utc) - created
    return timedelta(0) <= age < timedelta(hours=hours)


def items_to_query(params: Mapping[str, Any], **overrides: Any) -> str:
    """The query string for a link: the current filters, with some changed."""
    merged = {**dict(params), **overrides}
    return urlencode({key: value for key, value in merged.items() if value not in ("", None)})


TEMPLATES.env.filters["items_to_query"] = items_to_query
TEMPLATES.env.filters["when"] = _datetime_text
TEMPLATES.env.filters["initials"] = initials
TEMPLATES.env.filters["is_new"] = is_new
# Options for <select>: (value, text) pairs.
TEMPLATES.env.filters["zip_self"] = lambda items: [(item, item) for item in items]
TEMPLATES.env.filters["map_manager"] = lambda people: [
    (p["email"], f"{p['displayName']} — {p.get('jobTitle') or p.get('department') or p['email']}") for p in people]
TEMPLATES.env.filters["map_employee"] = lambda people: [
    (p["id"], f"{p['displayName']} — {p.get('department') or p['email']}") for p in people]
TEMPLATES.env.filters["map_profile"] = lambda profiles: [(p.id, p.label) for p in profiles]

# (href, label, icon, permission). The page refuses what the roles do not
# cover; hiding the link only spares a dead end.
NAV = [
    ("/", "Direktori", "directory", "directory.read"),
    ("/pengajuan", "Pengajuan", "approvals", "request.read"),
    ("/pengajuan/baru", "Pengajuan baru", "user-plus", "request.create"),
    ("/aktivitas", "Aktivitas", "clock", "activity.read"),
    ("/status", "Status sistem", "sync", "execution.run"),
]


def _nav_active(path: str, href: str) -> bool:
    if href == "/":
        return path == "/" or path.startswith("/karyawan/")
    if href == "/pengajuan":
        return path.startswith("/pengajuan") and not path.startswith("/pengajuan/baru")
    return path.startswith(href)


def brand_logo(env: Mapping[str, str]) -> str | None:
    """The logo NEXT_PUBLIC_BRAND_LOGO names (/brand/<file>), when that file is in static/brand."""
    configured = (env.get("NEXT_PUBLIC_BRAND_LOGO") or env.get("BRAND_LOGO") or "").strip()
    name = configured.rsplit("/", 1)[-1]
    if not name or name.startswith("your-") or not (APP_DIR / "static" / "brand" / name).is_file():
        return None
    return f"/static/brand/{name}"


@dataclass
class Context:
    settings: Settings
    ad: AdClient
    store: Store
    sessions: SessionStore
    mfa: MfaStore
    pending: PendingLogins
    throttle: Throttle
    security: SecurityLog
    lifecycle: Lifecycle
    dispatcher: Dispatcher
    worker: Worker
    background: dict
    env: Mapping[str, str]

    def session(self, request: Request) -> Session | None:
        return self.sessions.resolve(request.cookies.get(SESSION_COOKIE))

    def guard(self, request: Request, permission: str) -> tuple[Session | None, Response | None]:
        """The session, or the response to send instead: the login page, or a refusal."""
        session = self.session(request)
        if session is None:
            return None, RedirectResponse("/login", status_code=303)
        if not has_permission(session.roles, permission):
            return session, self.page(request, "denied.html", session=session, status=403)
        return session, None

    @staticmethod
    def actor(session: Session) -> dict:
        return {"name": session.full_name, "email": session.email.lower(), "userId": session.user_id}

    def page(self, request: Request, name: str, session: Session | None = None, status: int = 200, **context: Any) -> HTMLResponse:
        context.setdefault("brand", self.settings.brand_name or "HC User Management")
        context.setdefault("brand_logo", brand_logo(self.env))
        theme = request.cookies.get(THEME_COOKIE)
        context["theme"] = theme if theme in THEMES else None
        path = request.url.path
        context["current_path"] = path
        context["return_to"] = path + (f"?{request.url.query}" if request.url.query else "")
        if session is not None:
            context["session"] = session
            context["nav"] = [(href, label, icon, _nav_active(path, href))
                              for href, label, icon, permission in NAV if has_permission(session.roles, permission)]
            context["can"] = lambda permission: has_permission(session.roles, permission)
            if has_permission(session.roles, "request.read"):
                context["bell"] = notifications_for(self.store.read()["lifecycleRequests"])
        return TEMPLATES.TemplateResponse(request, name, context, status_code=status)
