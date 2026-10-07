"""The emails themselves: HTML with inline styles, because mail clients ignore
stylesheets. An approval email carries the whole request, so the approver can
decide from it — the portal is HC's alone and approvers never sign in."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.domain.lifecycle import TYPE_LABELS, jakarta_text
from app.domain.present import date_text, payload_rows
from app.mail.mailer import Mail

_env = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parents[1] / "templates" / "email"),
    autoescape=select_autoescape(["html"]),
)


def render_email(kind: str, content: dict, recipient: str, recipient_name: str | None, base_url: str) -> Mail:
    type_label = TYPE_LABELS.get(content["type"], content["type"])
    if kind == "approval.request":
        stage = "Manager" if content["stage"] == "MANAGER" else "CISO"
        link = f"{base_url}/persetujuan/{content['token']}"
        html = _env.get_template("approval_request.html").render(
            content=content, type_label=type_label, stage=stage, rows=payload_rows(content["payload"]),
            approve_url=f"{link}?putusan=setuju", reject_url=f"{link}?putusan=tolak",
            effective=date_text(content["effectiveAt"][:10]) if content.get("effectiveAt") else None,
            manager_decision=({"by": content["managerDecision"]["by"], "at": jakarta_text(content["managerDecision"]["at"])}
                              if content.get("managerDecision") else None),
        )
        subject = f"Persetujuan {stage} diperlukan: {type_label} — {content['subjectName']}"
    else:
        approved = content["outcome"] == "APPROVED"
        html = _env.get_template("approval_result.html").render(
            content=content, type_label=type_label, approved=approved,
            request_url=f"{base_url}/pengajuan/{content['requestId']}",
        )
        subject = f"Pengajuan {type_label} {content['subjectName']} {'disetujui' if approved else 'ditolak'}"
    return Mail(recipient, recipient_name, subject, html)
