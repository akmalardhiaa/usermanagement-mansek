"""A request end to end: raised by HC, approved from email, carried out in AD."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.domain.lifecycle import STATUS_LABELS
from app.domain.sync import sync_employees
from app.domain.validation import jakarta_today
from app.main import create_app
from app.settings import Settings

from conftest import ORIGIN, env_for
from fake_directory import DIRECTORY_ENV, GROUP_BASE, GROUP_ENGINEERING, PEOPLE, QUARANTINE, FakeDirectory

LINK = re.compile(r'href="http://testserver/persetujuan/([^"?]+)\?putusan=setuju"')


@pytest.fixture
def office(tmp_path):
    directory = FakeDirectory()
    directory.add("ayu.prameswari", "Ayu Prameswari", "ayu.prameswari@example.com", "Human Capital",
                  f"OU=Human Capital,{PEOPLE}", manager="dimas.anggara", title="Head of Human Capital")
    directory.add("dimas.anggara", "Dimas Anggara", "dimas.anggara@example.com", "Human Capital",
                  f"OU=Human Capital,{PEOPLE}", title="Head of Division")
    directory.add("sarah.wijaya", "Sarah Wijaya", "sarah.wijaya@example.com", "IT — Engineering",
                  f"OU=IT — Engineering,{PEOPLE}", title="Engineering Manager")
    directory.add("rizky.maulana", "Rizky Maulana", "rizky.maulana@example.com", "IT — Engineering",
                  f"OU=IT — Engineering,{PEOPLE}", manager="sarah.wijaya", title="Senior Backend Engineer",
                  groups=[GROUP_BASE, GROUP_ENGINEERING])
    env = env_for(tmp_path, LOGIN_2FA="off", DEMO_LOGIN="off", **DIRECTORY_ENV)
    app = create_app(Settings.from_env(env), directory, env=env, start_background=False)
    ctx = app.state.ctx
    sync_employees(ctx.store, directory, env)
    client = TestClient(app, base_url="http://testserver", follow_redirects=False)
    assert client.post("/login", data={"username": "hc", "password": "hc12345678"}, headers=ORIGIN).status_code == 303
    return client, ctx, directory, Path(env["EMAIL_FILE_DIR"])


def mails(folder: Path) -> list[dict]:
    """Every message the file mailer wrote, oldest first, with its approval token."""
    found = []
    for meta in sorted(folder.glob("*.json")):
        header = json.loads(meta.read_text(encoding="utf-8"))
        html = meta.with_suffix(".html").read_text(encoding="utf-8")
        match = LINK.search(html)
        found.append({**header, "html": html, "token": match.group(1) if match else None})
    return found


def send_and_collect(ctx, folder: Path, seen: int) -> list[dict]:
    ctx.dispatcher.dispatch_due()
    return mails(folder)[seen:]


def employee(ctx, email: str) -> dict:
    return next(e for e in ctx.store.read()["employees"] if e["email"] == email)


def onboarding_form(**overrides: str) -> dict:
    return {
        "jenis": "onboarding", "firstName": "Nadia", "lastName": "Putri", "displayName": "Nadia Putri",
        "email": "nadia.putri@example.com", "userId": "nadia.putri", "jobTitle": "Backend Engineer",
        "department": "IT — Engineering", "managerEmail": "sarah.wijaya@example.com",
        "employmentType": "PERMANENT", "locationType": "PUSAT", **overrides,
    }


def raise_request(client, form: dict) -> str:
    response = client.post("/pengajuan/baru", data=form, headers=ORIGIN)
    assert response.status_code == 303, response.text
    return response.headers["location"].split("/pengajuan/")[1].split("?")[0]


def approve(client, token: str):
    return client.post(f"/persetujuan/{token}", data={"decision": "APPROVED"}, headers=ORIGIN)


def test_onboarding_goes_from_the_form_to_an_account_in_the_division_ou(office):
    client, ctx, directory, folder = office
    request_id = raise_request(client, onboarding_form())
    page = client.get(f"/pengajuan/{request_id}?dikirim=1")
    assert page.status_code == 200
    assert STATUS_LABELS["PENDING_MANAGER"] in page.text and "Manager menerima email" in page.text

    # The manager's email, with a one-click link.
    sent = send_and_collect(ctx, folder, 0)
    assert [m["to"] for m in sent] == ["sarah.wijaya@example.com"]
    manager_token = sent[0]["token"]

    # Opening the page never decides — a link scanner fetching it changes nothing.
    preview = client.get(f"/persetujuan/{manager_token}")
    assert "script-src 'none'" in preview.headers["content-security-policy"]
    assert "approve.js" not in preview.text
    one_click = client.get(f"/persetujuan/{manager_token}?putusan=setuju")
    assert "script-src 'self'" in one_click.headers["content-security-policy"]
    assert "data-auto-submit" in one_click.text and "approve.js" in one_click.text
    assert ctx.lifecycle.get(request_id)["status"] == "PENDING_MANAGER"

    done = approve(client, manager_token)
    assert done.status_code == 200 and "Persetujuan tercatat" in done.text
    assert ctx.lifecycle.get(request_id)["status"] == "PENDING_CISO"
    again = client.get(f"/persetujuan/{manager_token}?putusan=setuju")
    assert "Keputusan sudah tercatat" in again.text and "approve.js" not in again.text

    # The whole CISO team is asked; the first answer is the one that counts.
    ciso = send_and_collect(ctx, folder, 1)
    assert sorted(m["to"] for m in ciso) == ["budi.ciso@example.com", "rina.ciso@example.com"]
    first, second = ciso
    assert approve(client, first["token"]).status_code == 200
    assert ctx.lifecycle.get(request_id)["status"] == "QUEUED"
    late = approve(client, second["token"])
    assert late.status_code == 409 and "Sudah disetujui oleh" in late.text
    assert "Keputusan sudah tercatat" in client.get(f"/persetujuan/{second['token']}").text

    # HC hears about it.
    result = send_and_collect(ctx, folder, 3)
    assert [m["to"] for m in result] == ["ayu.prameswari@example.com"]

    report = ctx.worker.run_due()
    assert report["completed"] == 1, report
    account = directory.find_by_name("nadia.putri")
    assert account["ou"] == f"OU=IT — Engineering,{PEOPLE}"
    assert account["enabled"] and account["groups"] == [GROUP_BASE]
    assert account["manager"] == "sarah.wijaya" and account["title"] == "Backend Engineer"
    created = employee(ctx, "nadia.putri@example.com")
    assert created["objectGUID"] == account["objectGUID"] and created["status"] == "ACTIVE"
    assert ctx.lifecycle.get(request_id)["status"] == "COMPLETED"
    assert "Nadia Putri dibuat" in client.get("/aktivitas").text
    assert "Selesai" in client.get(f"/pengajuan/{request_id}").text


def test_a_rejection_closes_the_request_and_touches_nothing(office):
    client, ctx, directory, folder = office
    request_id = raise_request(client, onboarding_form())
    token = send_and_collect(ctx, folder, 0)[0]["token"]

    confirm = client.get(f"/persetujuan/{token}?putusan=tolak")
    assert "Ya, tolak" in confirm.text and "approve.js" not in confirm.text
    assert ctx.lifecycle.get(request_id)["status"] == "PENDING_MANAGER"
    done = client.post(f"/persetujuan/{token}", data={"decision": "REJECTED"}, headers=ORIGIN)
    assert "Penolakan tercatat" in done.text
    assert ctx.lifecycle.get(request_id)["status"] == "REJECTED"
    assert ctx.worker.run_due()["ran"] == 0
    assert directory.writes == []
    assert [m["to"] for m in send_and_collect(ctx, folder, 1)] == ["ayu.prameswari@example.com"]


def test_a_decision_needs_the_page_of_the_portal(office):
    client, ctx, _, folder = office
    raise_request(client, onboarding_form())
    token = send_and_collect(ctx, folder, 0)[0]["token"]
    forged = client.post(f"/persetujuan/{token}", data={"decision": "APPROVED"}, headers={"Origin": "https://evil.example"})
    assert forged.status_code == 403


def test_movement_moves_the_account_to_the_new_division(office):
    client, ctx, directory, folder = office
    rizky = employee(ctx, "rizky.maulana@example.com")
    request_id = raise_request(client, {
        "jenis": "mutasi", "employeeId": rizky["id"], "toDepartment": "Risk Management", "toJobTitle": "Risk Officer",
        "toManagerEmail": "dimas.anggara@example.com",
    })
    manager = send_and_collect(ctx, folder, 0)
    assert [m["to"] for m in manager] == ["dimas.anggara@example.com"]
    approve(client, manager[0]["token"])
    approve(client, send_and_collect(ctx, folder, 1)[0]["token"])
    assert ctx.lifecycle.get(request_id)["status"] == "QUEUED"

    report = ctx.worker.run_due()
    assert report["completed"] == 1, report
    account = directory.find_by_name("rizky.maulana")
    assert account["ou"] == f"OU=Risk Management,{PEOPLE}"
    assert account["department"] == "Risk Management" and account["manager"] == "dimas.anggara"
    assert employee(ctx, "rizky.maulana@example.com")["department"] == "Risk Management"


def test_termination_disables_revokes_and_quarantines(office):
    client, ctx, directory, folder = office
    rizky = employee(ctx, "rizky.maulana@example.com")
    yesterday = (jakarta_today() - timedelta(days=1)).isoformat()
    request_id = raise_request(client, {
        "jenis": "resign", "employeeId": rizky["id"], "lastWorkingDate": yesterday, "reasonCategory": "RESIGN",
        "note": "Catatan internal yang tidak boleh ke email",
    })
    manager = send_and_collect(ctx, folder, 0)
    assert "Catatan internal" not in manager[0]["html"]
    approve(client, manager[0]["token"])
    approve(client, send_and_collect(ctx, folder, 1)[0]["token"])

    assert ctx.worker.run_due()["completed"] == 1
    account = directory.find_by_name("rizky.maulana")
    assert not account["enabled"] and account["ou"] == QUARANTINE and account["groups"] == []
    assert directory.writes[0][0] == "enabled"  # disabling comes first
    assert employee(ctx, "rizky.maulana@example.com")["status"] == "DISABLED"
    assert ctx.lifecycle.get(request_id)["status"] == "COMPLETED"


def test_a_failed_run_can_be_retried_and_skips_what_was_done(office):
    client, ctx, directory, folder = office
    request_id = raise_request(client, onboarding_form())
    approve(client, send_and_collect(ctx, folder, 0)[0]["token"])
    approve(client, send_and_collect(ctx, folder, 1)[0]["token"])

    directory.fail_on = "groups"  # the account exists by then, without its groups
    assert ctx.worker.run_due()["failed"] == 1
    assert ctx.lifecycle.get(request_id)["status"] == "FAILED"
    page = client.get(f"/pengajuan/{request_id}")
    assert "Domain controller tidak bisa dihubungi" in page.text

    # HC cannot retry: that is the operator's job.
    assert client.post(f"/pengajuan/{request_id}/ulang", headers=ORIGIN).status_code == 403
    ctx.lifecycle.retry(request_id, {"name": "Ops", "email": "ops@example.com", "userId": "ops"})
    assert ctx.worker.run_due()["completed"] == 1
    kinds = [w[0] for w in directory.writes]
    assert kinds.count("create") == 1 and kinds.count("attributes") == 1  # done steps are not repeated
    assert directory.find_by_name("nadia.putri")["groups"] == [GROUP_BASE]


def test_the_requester_can_cancel_and_the_links_stop_working(office):
    client, ctx, _, folder = office
    request_id = raise_request(client, onboarding_form())
    token = send_and_collect(ctx, folder, 0)[0]["token"]
    response = client.post(f"/pengajuan/{request_id}/batal", headers=ORIGIN)
    assert response.status_code == 303 and "pesan=" in response.headers["location"]
    assert ctx.lifecycle.get(request_id)["status"] == "CANCELLED"
    assert approve(client, token).status_code == 409


def test_a_revision_goes_back_to_the_manager_with_new_links(office):
    client, ctx, _, folder = office
    request_id = raise_request(client, onboarding_form())
    old = send_and_collect(ctx, folder, 0)[0]["token"]
    form = client.get(f"/pengajuan/{request_id}/revisi")
    assert form.status_code == 200 and 'value="Nadia"' in form.text
    response = client.post(f"/pengajuan/{request_id}/revisi", data=onboarding_form(jobTitle="Senior Backend Engineer"),
                           headers=ORIGIN)
    assert response.status_code == 303
    item = ctx.lifecycle.get(request_id)
    assert item["version"] == 2 and item["payload"]["jobTitle"] == "Senior Backend Engineer"
    assert approve(client, old).status_code == 409
    fresh = send_and_collect(ctx, folder, 1)
    assert [m["to"] for m in fresh] == ["sarah.wijaya@example.com"] and fresh[0]["token"] != old


def test_the_form_says_what_is_wrong(office):
    client, _, _, _ = office
    response = client.post("/pengajuan/baru", data=onboarding_form(email="bukan-email", userId="X"), headers=ORIGIN)
    assert response.status_code == 422
    assert "Email tidak valid" in response.text and "User ID hanya boleh" in response.text


def test_one_open_request_per_employee(office):
    client, ctx, _, _ = office
    rizky = employee(ctx, "rizky.maulana@example.com")
    form = {"jenis": "mutasi", "employeeId": rizky["id"], "toDepartment": "Finance", "toJobTitle": "Analyst",
            "toManagerEmail": "dimas.anggara@example.com"}
    raise_request(client, form)
    second = client.post("/pengajuan/baru", data=form, headers=ORIGIN)
    assert second.status_code == 409


def approve_both(client, ctx, folder):
    seen = len(mails(folder))
    approve(client, send_and_collect(ctx, folder, seen)[0]["token"])
    approve(client, send_and_collect(ctx, folder, seen + 1)[0]["token"])


def test_a_missing_division_ou_stops_the_run_before_anything_changes(office):
    client, ctx, directory, folder = office
    rizky = employee(ctx, "rizky.maulana@example.com")
    missing = f"OU=Legal,{PEOPLE}"
    directory.missing_ous.add(missing)
    request_id = raise_request(client, {"jenis": "mutasi", "employeeId": rizky["id"], "toDepartment": "Legal",
                                        "toJobTitle": "Legal Officer", "toManagerEmail": "dimas.anggara@example.com"})
    approve_both(client, ctx, folder)
    writes_before = list(directory.writes)
    assert ctx.worker.run_due()["failed"] == 1
    assert directory.writes == writes_before  # the account was not touched
    page = client.get(f"/pengajuan/{request_id}").text
    assert "OU tujuan belum ada" in page and "Coba lagi" in page

    directory.missing_ous.clear()  # IT creates the OU
    ctx.lifecycle.retry(request_id, {"name": "Ops", "email": "ops@example.com", "userId": "ops"})
    assert ctx.worker.run_due()["completed"] == 1
    assert directory.find_by_name("rizky.maulana")["ou"] == missing


def test_a_retry_after_a_half_finished_run_is_not_mistaken_for_someone_elses_change(office, tmp_path):
    """The run wrote the new division, then stopped; the AD sync copied it into the record."""
    client, ctx, directory, folder = office
    rizky = employee(ctx, "rizky.maulana@example.com")
    request_id = raise_request(client, {"jenis": "mutasi", "employeeId": rizky["id"], "toDepartment": "Risk Management",
                                        "toJobTitle": "Risk Officer", "toManagerEmail": "dimas.anggara@example.com"})
    approve_both(client, ctx, folder)
    directory.fail_on = "move"
    assert ctx.worker.run_due()["failed"] == 1
    sync_employees(ctx.store, directory, {**DIRECTORY_ENV, "AD_SYNC_OUS": PEOPLE})
    assert employee(ctx, "rizky.maulana@example.com")["department"] == "Risk Management"

    ctx.lifecycle.retry(request_id, {"name": "Ops", "email": "ops@example.com", "userId": "ops"})
    report = ctx.worker.run_due()
    assert report["completed"] == 1, report
    assert directory.find_by_name("rizky.maulana")["ou"] == f"OU=Risk Management,{PEOPLE}"


def test_a_change_somebody_else_made_still_stops_the_run(office):
    client, ctx, directory, folder = office
    rizky = employee(ctx, "rizky.maulana@example.com")
    request_id = raise_request(client, {"jenis": "mutasi", "employeeId": rizky["id"], "toDepartment": "Risk Management",
                                        "toJobTitle": "Risk Officer", "toManagerEmail": "dimas.anggara@example.com"})
    approve_both(client, ctx, folder)
    ctx.store.mutate(lambda data: next(e for e in data["employees"] if e["id"] == rizky["id"]).update(department="Finance"))
    assert ctx.worker.run_due()["failed"] == 1
    assert "berubah sejak pengajuan disetujui" in client.get(f"/pengajuan/{request_id}").text


def test_structured_external_branch_onboarding_and_internal_hq_movement(office):
    client, ctx, directory, folder = office
    base = "DC=corp,DC=example"
    external_batam = f"OU=Batam,OU=Cabang,OU=External,{base}"
    internal_retail = f"OU=Retail,OU=Kantor Pusat,{base}"
    directory.ous.update({external_batam, internal_retail})
    ctx.env.update({
        "AD_OU_ROUTING_MODE": "STRUCTURED",
        "AD_OU_INTERNAL_PUSAT_PARENT": f"OU=Kantor Pusat,{base}",
        "AD_OU_INTERNAL_CABANG_PARENT": f"OU=Kantor Cabang,{base}",
        "AD_OU_EXTERNAL_PUSAT": f"OU=Pusat,OU=External,{base}",
        "AD_OU_EXTERNAL_CABANG_PARENT": f"OU=Cabang,OU=External,{base}",
    })
    request_id = raise_request(client, onboarding_form(
        workerCategory="EXTERNAL", locationType="CABANG", branchName="Batam",
        department="Generic"))
    approve_both(client, ctx, folder)
    assert ctx.worker.run_due()["completed"] == 1
    account = directory.find_by_name("nadia.putri")
    assert account["ou"] == external_batam
    assert account["groups"] == [GROUP_BASE]
    assert employee(ctx, "nadia.putri@example.com")["workerCategory"] == "EXTERNAL"

    created = employee(ctx, "nadia.putri@example.com")
    request_id = raise_request(client, {
        "jenis": "mutasi", "employeeId": created["id"], "toDepartment": "Retail",
        "toJobTitle": "Analyst", "toManagerEmail": "dimas.anggara@example.com",
        "toWorkerCategory": "INTERNAL", "toLocationType": "PUSAT",
    })
    seen = len(mails(folder))
    manager_mail = next(m for m in send_and_collect(ctx, folder, seen)
                        if m["token"] and ctx.lifecycle.preview_by_token(m["token"]).get("request", {}).get("id") == request_id)
    assert approve(client, manager_mail["token"]).status_code == 200
    seen = len(mails(folder))
    ciso_mail = next(m for m in send_and_collect(ctx, folder, seen)
                     if m["token"] and ctx.lifecycle.preview_by_token(m["token"]).get("request", {}).get("id") == request_id)
    assert approve(client, ciso_mail["token"]).status_code == 200
    assert ctx.lifecycle.get(request_id)["status"] == "QUEUED", ctx.lifecycle.get(request_id)
    report = ctx.worker.run_due()
    assert report["completed"] == 1, report
    account = directory.find_by_name("nadia.putri")
    assert account["ou"] == internal_retail
    assert account["groups"] == [GROUP_BASE]
    assert employee(ctx, "nadia.putri@example.com")["workerCategory"] == "INTERNAL"


def test_intern_account_is_written_only_after_approvals_with_ad_attributes(office):
    client, ctx, directory, folder = office
    intern_ou = "OU=Intern,DC=corp,DC=example"
    directory.ous.add(intern_ou)
    ctx.env.update({
        "AD_OU_ROUTING_MODE": "STRUCTURED",
        "AD_OU_INTERN": intern_ou,
        "AD_COMPANY_NAME": "PT Mandiri Sekuritas",
        "LDAP_DOMAIN": "mandirisekuritas.co.id",
    })
    form = onboarding_form(workerCategory="INTERN", locationType="PUSAT", department="Generic",
                           userId="AMUMUBAROK367", jobDescription="TI Helpdesk", employmentType="CONTRACT",
                           expiredDate="2026-12-31")
    request_id = raise_request(client, form)
    assert ctx.lifecycle.get(request_id)["status"] == "PENDING_MANAGER"
    assert not directory.writes
    approve_both(client, ctx, folder)
    assert not directory.writes
    assert ctx.worker.run_due()["completed"] == 1
    account = directory.find_by_name("AMUMUBAROK367")
    assert account["ou"] == intern_ou
    assert account["userPrincipalName"] == "amumubarok367@mandirisekuritas.co.id"
    assert account["company"] == "PT Mandiri Sekuritas"
    assert account["office"] == "Kantor Pusat"
    assert account["description"] == "TI Helpdesk"
    assert account["accountExpires"] == "134432100000000000"
    assert employee(ctx, "nadia.putri@example.com")["workerCategory"] == "INTERN"
