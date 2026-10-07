"""Lifecycle requests in the portal: list, new, detail, cancel, retry, revise."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.domain.catalog import (ACCESS_PROFILES, DEPARTMENTS, EMPLOYMENT_TYPES, JOB_TITLES, LOCATIONS,
                                TERMINATION_REASONS)
from app.domain.lifecycle import LifecycleError, TYPE_LABELS, is_active
from app.domain.present import payload_rows
from app.domain.validation import date_bounds, parse_request
from app.web.context import Context

KINDS = {"onboarding": "ONBOARDING", "mutasi": "MOVEMENT", "resign": "TERMINATION", "profil": "PROFILE_UPDATE"}
SLUG = {value: key for key, value in KINDS.items()}
STATUS_GROUPS = {
    "aktif": None,  # everything not finished
    "selesai": {"COMPLETED"},
    "gagal": {"FAILED"},
    "ditutup": {"REJECTED", "CANCELLED", "EXPIRED"},
}


def _back(request_id: str, key: str, text: str) -> RedirectResponse:
    """Back to the request page with a message; the text may hold any character."""
    return RedirectResponse(f"/pengajuan/{request_id}?{key}={quote(str(text), safe='')}", status_code=303)


def _form_values(payload: dict) -> dict:
    """A stored payload as the form's field values, for a revision."""
    values = {k: v for k, v in payload.items() if isinstance(v, str)}
    if payload.get("kind") == "PROFILE_UPDATE":
        values.update({k: v for k, v in payload.get("profile", {}).items() if isinstance(v, str)})
    return values


def register(app: FastAPI, ctx: Context) -> None:
    def form_context(kind: str, values: dict, errors: dict) -> dict:
        data = ctx.store.read()
        active_people = sorted((e for e in data["employees"] if e["status"] == "ACTIVE"), key=lambda e: e["displayName"].lower())
        return {
            "kind": kind, "slug": SLUG[kind], "values": values, "errors": errors, "type_label": TYPE_LABELS[kind],
            "employees": active_people, "managers": active_people, "departments": DEPARTMENTS, "job_titles": JOB_TITLES,
            "profiles": ACCESS_PROFILES, "employment": EMPLOYMENT_TYPES, "locations": LOCATIONS,
            "reasons": TERMINATION_REASONS, "bounds": {name: date_bounds(name) for name in
                                                       ("effectiveAt", "startDate", "lastWorkingDate", "newContractEnd")},
        }

    def with_manager_names(values: dict) -> dict:
        """The form picks a manager by email; the name comes from the directory."""
        people = {e["email"].lower(): e["displayName"] for e in ctx.store.read()["employees"]}
        for email_key, name_key in (("managerEmail", "managerName"), ("toManagerEmail", "toManagerName")):
            email = (values.get(email_key) or "").strip().lower()
            if email:
                values[name_key] = people.get(email, values.get(name_key, ""))
        return values

    @app.get("/pengajuan", response_class=HTMLResponse)
    def list_requests(request: Request):
        session, refused = ctx.guard(request, "request.read")
        if refused:
            return refused
        kind = KINDS.get(request.query_params.get("jenis", ""))
        group = request.query_params.get("status", "aktif")
        requests = ctx.lifecycle.list(kind)
        if group == "aktif":
            requests = [r for r in requests if is_active(r["status"])]
        elif group in STATUS_GROUPS:
            requests = [r for r in requests if r["status"] in STATUS_GROUPS[group]]
        return ctx.page(request, "requests.html", session=session, requests=requests[:200], total=len(requests),
                        jenis=request.query_params.get("jenis", ""), group=group, kinds=KINDS)

    @app.get("/pengajuan/baru", response_class=HTMLResponse)
    def new_form(request: Request):
        session, refused = ctx.guard(request, "request.create")
        if refused:
            return refused
        slug = request.query_params.get("jenis", "")
        if slug not in KINDS:
            return ctx.page(request, "request_kind.html", session=session, kinds=KINDS,
                            employee_id=request.query_params.get("karyawan", ""))
        kind = KINDS[slug]
        values: dict = {}
        employee_id = request.query_params.get("karyawan", "")
        if employee_id:
            values["employeeId"] = employee_id
            employee = next((e for e in ctx.store.read()["employees"] if e["id"] == employee_id), None)
            if employee and kind == "PROFILE_UPDATE":
                values.update({k: v for k, v in employee.items() if isinstance(v, str)})
            if employee and kind == "MOVEMENT":
                values.update(toDepartment=employee.get("department", ""), toJobTitle=employee.get("jobTitle", ""),
                              toManagerEmail=employee.get("managerEmail", ""))
        return ctx.page(request, "request_form.html", session=session, action="/pengajuan/baru", **form_context(kind, values, {}))

    @app.post("/pengajuan/baru", response_class=HTMLResponse)
    async def create(request: Request):
        session, refused = ctx.guard(request, "request.create")
        if refused:
            return refused
        form = with_manager_names({k: str(v) for k, v in (await request.form()).items()})
        kind = KINDS.get(form.get("jenis", ""), "")
        if not kind:
            return RedirectResponse("/pengajuan/baru", status_code=303)
        parsed = parse_request(kind, form)
        if not parsed.ok:
            return ctx.page(request, "request_form.html", session=session, status=422, action="/pengajuan/baru",
                            **form_context(kind, form, parsed.errors))
        try:
            created = ctx.lifecycle.create_and_submit(parsed.payload, parsed.effective_at, ctx.actor(session))
        except LifecycleError as error:
            return ctx.page(request, "request_form.html", session=session, status=409 if error.code == "CONFLICT" else 422,
                            action="/pengajuan/baru", **form_context(kind, form, {"_": str(error)}))
        return RedirectResponse(f"/pengajuan/{created['id']}?dikirim=1", status_code=303)

    @app.get("/pengajuan/{request_id}", response_class=HTMLResponse)
    def detail(request: Request, request_id: str):
        session, refused = ctx.guard(request, "request.read")
        if refused:
            return refused
        try:
            item = ctx.lifecycle.get(request_id)
        except LifecycleError:
            return ctx.page(request, "not_found.html", session=session, status=404)
        jobs = ctx.lifecycle.jobs(request_id)
        job = next((j for j in jobs if j["operationId"] == f"{item['id']}:{item['version']}"), None)
        return ctx.page(request, "request_detail.html", session=session, item=item, rows=payload_rows(item["payload"], internal=True),
                        job=job, trail=list(reversed(ctx.lifecycle.audit_trail(request_id))),
                        sent=request.query_params.get("dikirim") == "1",
                        message=request.query_params.get("pesan"), error=request.query_params.get("galat"),
                        is_requester=item["requester"].get("userId") == session.user_id)

    @app.post("/pengajuan/{request_id}/batal")
    def cancel(request: Request, request_id: str):
        session, refused = ctx.guard(request, "request.cancel")
        if refused:
            return refused
        try:
            ctx.lifecycle.cancel(request_id, ctx.actor(session), "SYSTEM_ADMIN" in session.roles)
        except LifecycleError as error:
            return _back(request_id, "galat", error)
        return _back(request_id, "pesan", "Pengajuan dibatalkan.")

    @app.post("/pengajuan/{request_id}/ulang")
    async def retry(request: Request, request_id: str):
        session, refused = ctx.guard(request, "execution.run")
        if refused:
            return refused
        acknowledged = (await request.form()).get("rekonsiliasi") == "ya"
        try:
            ctx.lifecycle.retry(request_id, ctx.actor(session), acknowledged)
        except LifecycleError as error:
            return _back(request_id, "galat", error)
        ctx.background["worker"].kick()
        return _back(request_id, "pesan", "Dimasukkan kembali ke antrean worker.")

    @app.get("/pengajuan/{request_id}/revisi", response_class=HTMLResponse)
    def revise_form(request: Request, request_id: str):
        session, refused = ctx.guard(request, "request.create")
        if refused:
            return refused
        try:
            item = ctx.lifecycle.get(request_id)
        except LifecycleError:
            return ctx.page(request, "not_found.html", session=session, status=404)
        values = _form_values(item["payload"])
        if item.get("effectiveAt") and item["type"] != "TERMINATION":
            values["effectiveAt"] = item["effectiveAt"][:10]
        return ctx.page(request, "request_form.html", session=session, action=f"/pengajuan/{request_id}/revisi",
                        revising=item, **form_context(item["type"], values, {}))

    @app.post("/pengajuan/{request_id}/revisi", response_class=HTMLResponse)
    async def revise(request: Request, request_id: str):
        session, refused = ctx.guard(request, "request.create")
        if refused:
            return refused
        try:
            item = ctx.lifecycle.get(request_id)
        except LifecycleError:
            return ctx.page(request, "not_found.html", session=session, status=404)
        form = with_manager_names({k: str(v) for k, v in (await request.form()).items()})
        parsed = parse_request(item["type"], form)
        if not parsed.ok:
            return ctx.page(request, "request_form.html", session=session, status=422, revising=item,
                            action=f"/pengajuan/{request_id}/revisi", **form_context(item["type"], form, parsed.errors))
        try:
            ctx.lifecycle.revise_and_resubmit(request_id, parsed.payload, parsed.effective_at, ctx.actor(session))
        except LifecycleError as error:
            return ctx.page(request, "request_form.html", session=session, status=409, revising=item,
                            action=f"/pengajuan/{request_id}/revisi", **form_context(item["type"], form, {"_": str(error)}))
        return _back(request_id, "pesan", "Revisi dikirim ulang ke manager.")
