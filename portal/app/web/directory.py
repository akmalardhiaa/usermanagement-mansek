"""The employee directory: search, filter, sort, one page at a time, and export.

Filtering and sorting run here, on the server, over everybody; the page draws
fifty rows. A directory read from AD with a thousand people stays quick.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from app.domain.catalog import DEPARTMENTS, EMPLOYMENT_TYPES, LOCATIONS
from app.domain.lifecycle import is_active
from app.domain.store import now_iso, record_activity
from app.web.context import Context

PAGE_SIZE = 50
SORTS = {"nama": "displayName", "departemen": "department", "jabatan": "jobTitle", "status": "status"}


def _filtered(employees: list[dict], query: dict) -> list[dict]:
    text = (query.get("q") or "").strip().lower()
    department = (query.get("departemen") or "").strip()
    status = query.get("status") or "aktif"
    found = []
    for employee in employees:
        if status == "aktif" and employee["status"] != "ACTIVE":
            continue
        if status == "nonaktif" and employee["status"] != "DISABLED":
            continue
        if department and employee.get("department") != department:
            continue
        if text and not any(text in (employee.get(f) or "").lower() for f in ("displayName", "email", "jobTitle", "department")):
            continue
        found.append(employee)
    key = SORTS.get(query.get("urut") or "nama", "displayName")
    return sorted(found, key=lambda e: ((e.get(key) or "").lower(), e["displayName"].lower()),
                  reverse=query.get("arah") == "turun")


def register(app: FastAPI, ctx: Context) -> None:
    @app.get("/", response_class=HTMLResponse)
    def directory(request: Request):
        session, refused = ctx.guard(request, "directory.read")
        if refused:
            return refused
        data = ctx.store.read()
        query = dict(request.query_params)
        employees = _filtered(data["employees"], query)
        try:
            page = max(1, int(query.get("halaman") or 1))
        except ValueError:
            page = 1
        pages = max(1, -(-len(employees) // PAGE_SIZE))
        page = min(page, pages)
        active_requests = {r["employeeId"]: r for r in data["lifecycleRequests"]
                           if r.get("employeeId") and is_active(r["status"])}
        month_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        stats = {
            "active": sum(1 for e in data["employees"] if e["status"] == "ACTIVE"),
            "disabled": sum(1 for e in data["employees"] if e["status"] == "DISABLED"),
            "pending": sum(1 for r in data["lifecycleRequests"] if is_active(r["status"])),
            "new": sum(1 for e in data["employees"] if e.get("createdAt", "") >= month_ago),
        }
        base_query = {k: v for k, v in query.items() if k != "halaman" and v}
        return ctx.page(request, "directory.html", session=session, employees=employees[(page - 1) * PAGE_SIZE: page * PAGE_SIZE],
                        total=len(employees), page=page, pages=pages, query=query, base_query=base_query,
                        departments=sorted({e["department"] for e in data["employees"] if e.get("department")} | set(DEPARTMENTS)),
                        stats=stats, active_requests=active_requests)

    @app.get("/karyawan/{employee_id}", response_class=HTMLResponse)
    def employee_page(request: Request, employee_id: str):
        session, refused = ctx.guard(request, "directory.read")
        if refused:
            return refused
        data = ctx.store.read()
        employee = next((e for e in data["employees"] if e["id"] == employee_id), None)
        if employee is None:
            return ctx.page(request, "not_found.html", session=session, status=404)
        history = sorted((r for r in data["lifecycleRequests"] if r.get("employeeId") == employee_id),
                         key=lambda r: r["createdAt"], reverse=True)
        active = next((r for r in history if is_active(r["status"])), None)
        return ctx.page(request, "employee.html", session=session, employee=employee, history=history, active=active,
                        employment=EMPLOYMENT_TYPES, locations=LOCATIONS)

    @app.get("/direktori/export.xlsx")
    def export(request: Request):
        session, refused = ctx.guard(request, "directory.export")
        if refused:
            return refused
        employees = _filtered(ctx.store.read()["employees"], dict(request.query_params))
        book = Workbook()
        sheet = book.active
        sheet.title = "Direktori"
        headers = ["Nama", "Email", "Jabatan", "Departemen", "Status", "Status kepegawaian", "Lokasi", "Manager", "Email manager"]
        sheet.append(headers)
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="173D6E")
        for e in employees:
            sheet.append([e["displayName"], e["email"], e.get("jobTitle", ""), e.get("department", ""),
                          "Aktif" if e["status"] == "ACTIVE" else "Nonaktif",
                          EMPLOYMENT_TYPES.get(e.get("employmentType") or "", ""), LOCATIONS.get(e.get("locationType") or "", ""),
                          e.get("managerName", ""), e.get("managerEmail", "")])
            # Text stays text: a name from AD that starts with "=" must not become a formula.
            for cell in sheet[sheet.max_row]:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
        for column, width in zip("ABCDEFGHI", (28, 34, 26, 22, 10, 20, 14, 24, 32)):
            sheet.column_dimensions[column].width = width
        buffer = io.BytesIO()
        book.save(buffer)
        buffer.seek(0)
        ctx.store.mutate(lambda data: record_activity(
            data, session.full_name, "directory.exported", f"{len(employees)} baris diekspor ke Excel."))
        name = f"direktori-karyawan-{now_iso()[:10]}.xlsx"
        return StreamingResponse(buffer, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                 headers={"Content-Disposition": f'attachment; filename="{name}"'})
