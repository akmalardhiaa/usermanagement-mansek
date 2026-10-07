"""What every page handler needs: the services, the session, and rendering."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.ad_client import AdClient
from app.domain.lifecycle import STATUS_LABELS, TYPE_LABELS, Lifecycle
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

TEMPLATES = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")
TEMPLATES.env.globals.update(STATUS_LABELS=STATUS_LABELS, TYPE_LABELS=TYPE_LABELS, ROLE_LABELS=ROLE_LABELS,
                             date_text=date_text)


def _datetime_text(iso: str | None) -> str:
    if not iso:
        return "—"
    from app.domain.lifecycle import jakarta_text

    return f"{jakarta_text(iso)} WIB"


TEMPLATES.env.filters["when"] = _datetime_text
# Options for <select>: (value, text) pairs.
TEMPLATES.env.filters["zip_self"] = lambda items: [(item, item) for item in items]
TEMPLATES.env.filters["map_manager"] = lambda people: [
    (p["email"], f"{p['displayName']} — {p.get('jobTitle') or p.get('department') or p['email']}") for p in people]
TEMPLATES.env.filters["map_employee"] = lambda people: [
    (p["id"], f"{p['displayName']} — {p.get('department') or p['email']}") for p in people]
TEMPLATES.env.filters["map_profile"] = lambda profiles: [(p.id, p.label) for p in profiles]

NAV = [
    ("/", "Direktori", "directory.read"),
    ("/pengajuan", "Pengajuan", "request.read"),
    ("/aktivitas", "Aktivitas", "activity.read"),
    ("/status", "Status sistem", "execution.run"),
]


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
        if session is not None:
            context["session"] = session
            context["nav"] = [(href, label) for href, label, permission in NAV if has_permission(session.roles, permission)]
            context["can"] = lambda permission: has_permission(session.roles, permission)
            context["current_path"] = request.url.path
        return TEMPLATES.TemplateResponse(request, name, context, status_code=status)
