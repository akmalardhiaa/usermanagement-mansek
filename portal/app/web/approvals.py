"""The page an approval email links to — the only place a decision is made.

Approvers have no portal account: the single-use link issued to their own
address is what authenticates them. "Setujui" in the email decides in one
click: the page posts the approval itself, with a five-line script. A link
scanner fetching the URL does not run the script, so it cannot approve
anything — which is why a plain GET never decides. Rejecting asks for one
confirming click.
"""

from __future__ import annotations

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse

from app.domain.lifecycle import LifecycleError
from app.domain.present import payload_rows
from app.security.web import CSP_WITH_SCRIPT
from app.web.context import Context


def register(app: FastAPI, ctx: Context) -> None:
    @app.get("/persetujuan/{token}", response_class=HTMLResponse)
    def approval_page(request: Request, token: str):
        preview = ctx.lifecycle.preview_by_token(token)
        intent = {"setuju": "APPROVED", "tolak": "REJECTED"}.get(request.query_params.get("putusan", ""))
        rows = payload_rows(preview["request"]["payload"]) if preview["ok"] else []
        response = ctx.page(request, "approval.html", preview=preview, intent=intent, token=token, rows=rows)
        if preview["ok"] and intent == "APPROVED":
            response.headers["Content-Security-Policy"] = CSP_WITH_SCRIPT
        return response

    @app.post("/persetujuan/{token}", response_class=HTMLResponse)
    def decide(request: Request, token: str, decision: str = Form("")):
        try:
            decided = ctx.lifecycle.decide_by_token(token, decision)
        except LifecycleError as error:
            return ctx.page(request, "approval_done.html", status=409 if error.code == "CONFLICT" else 400,
                            ok=False, message=str(error))
        if decided["status"] == "QUEUED":
            ctx.background["worker"].kick()
        return ctx.page(request, "approval_done.html", ok=True, decision=decision, item=decided)
