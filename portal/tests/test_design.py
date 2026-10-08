"""The dashboard's look that carries meaning: theme, filters as links, the bell, the feed."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.notifications import notifications_for
from app.domain.store import now_iso

from conftest import ORIGIN


@pytest.fixture
def hc(make_client):
    client = make_client(LOGIN_2FA="off")
    assert client.post("/login", data={"username": "hc", "password": "hc12345678"}, headers=ORIGIN).status_code == 303
    return client


def test_the_theme_is_remembered_without_a_script(hc):
    assert "data-theme" not in hc.get("/").text.split(">", 2)[1]  # follows the computer until chosen
    response = hc.post("/tema", data={"tema": "light", "kembali": "/pengajuan?status=semua"}, headers=ORIGIN)
    assert response.status_code == 303 and response.headers["location"] == "/pengajuan?status=semua"
    assert '<html lang="id" data-theme="light">' in hc.get("/").text
    hc.post("/tema", data={"tema": "dark", "kembali": "/"}, headers=ORIGIN)
    assert 'data-theme="dark"' in hc.get("/").text


def test_the_theme_form_never_sends_anyone_elsewhere(hc):
    for target in ("https://evil.example/", "//evil.example", "/\\evil.example", ""):
        response = hc.post("/tema", data={"tema": "light", "kembali": target}, headers=ORIGIN)
        assert response.headers["location"] == "/"


def test_every_page_is_free_of_inline_scripts_and_styles(hc):
    """The CSP forbids both; a style attribute would silently not apply."""
    for path in ("/", "/pengajuan", "/pengajuan/baru", "/pengajuan/baru?jenis=onboarding", "/aktivitas", "/login"):
        page = hc.get(path).text
        assert " style=" not in page, path
        assert "<script" not in page, path


def test_the_stat_cards_are_the_filters(hc):
    ctx = hc.app.state.ctx

    disabled = "Rizky Maulana"

    def disable(data):
        next(e for e in data["employees"] if e["displayName"] == disabled)["status"] = "DISABLED"
    ctx.store.mutate(disable)

    first = hc.get("/").text
    assert disabled not in first and "2 nonaktif disembunyikan" in first  # Rizky and the demo roster's Clara
    assert 'href="/?status=nonaktif"' in first
    only_disabled = hc.get("/?status=nonaktif").text
    assert disabled in only_disabled and 'class="stat selected' in only_disabled
    # Pressing the selected card again clears it.
    assert re.search(r'class="stat selected[^"]*" href="/"', only_disabled)
    assert disabled in hc.get("/", params={"q": disabled.split()[0]}).text


def test_the_pending_card_lists_people_with_a_request_in_flight(hc):
    ctx = hc.app.state.ctx
    rizky = next(e for e in ctx.store.read()["employees"] if e["displayName"] == "Rizky Maulana")
    hc.post("/pengajuan/baru", data={"jenis": "mutasi", "employeeId": rizky["id"], "toDepartment": "Finance",
                                     "toJobTitle": "Analyst", "toManagerEmail": "dimas.anggara@example.com"}, headers=ORIGIN)
    page = hc.get("/?status=pengajuan").text
    assert "Rizky Maulana" in page and "Dimas Anggara</span>" not in page
    assert 'class="pending-badge"' in page


def test_sorting_by_a_column_flips_on_the_second_click(hc):
    page = hc.get("/?urut=nama").text
    assert 'href="/?urut=nama&amp;arah=turun"' in page
    names = re.findall(r'class="person-name"><span>([^<]+)</span>', hc.get("/?urut=nama&arah=turun").text)
    assert names == sorted(names, key=str.lower, reverse=True)


def test_a_new_hire_in_flight_shows_in_the_pipeline(hc):
    hc.post("/pengajuan/baru", data={
        "jenis": "onboarding", "firstName": "Nadia", "lastName": "Putri", "displayName": "Nadia Putri",
        "email": "nadia.putri@example.com", "userId": "nadia.putri", "jobTitle": "Backend Engineer",
        "department": "IT — Engineering", "managerEmail": "sarah.wijaya@example.com",
        "employmentType": "PERMANENT", "locationType": "PUSAT"}, headers=ORIGIN)
    page = hc.get("/").text
    assert "Karyawan baru dalam proses" in page and "Nadia Putri" in page
    assert "Menunggu persetujuan Sarah Wijaya" in page


def test_the_bell_counts_what_needs_somebody():
    now = datetime(2026, 10, 8, 3, tzinfo=timezone.utc)

    def request(status, hours_ago, rid):
        at = (now - timedelta(hours=hours_ago)).isoformat()
        return {"id": rid, "type": "ONBOARDING", "status": status, "updatedAt": at,
                "subject": {"displayName": rid, "department": "IT"}}

    feed = notifications_for([
        request("FAILED", 1, "a"), request("PENDING_MANAGER", 30, "b"), request("PENDING_CISO", 2, "c"),
        request("COMPLETED", 10, "d"), request("COMPLETED", 60, "e"), request("REJECTED", 5, "f"),
    ], now)
    assert [item["id"] for item in feed["items"]] == ["a", "b", "f", "d"]
    assert feed["needsAttention"] == 2


def test_the_activity_feed_groups_by_day_and_filters(hc):
    ctx = hc.app.state.ctx

    def seed(data):
        data["activity"] += [
            {"id": "a1", "at": now_iso(), "actor": "Ayu", "action": "user.created", "employeeId": None,
             "employeeName": "Nadia", "detail": "Akun Nadia Putri dibuat di Active Directory."},
            {"id": "a2", "at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(), "actor": "Sinkronisasi AD",
             "action": "directory.synced", "employeeId": None, "employeeName": None, "detail": "Dari Active Directory: 3 karyawan baru."},
        ]
    ctx.store.mutate(seed)
    page = hc.get("/aktivitas").text
    assert "Hari ini" in page and "Kemarin" in page and "Akun dibuat" in page
    only = hc.get("/aktivitas?jenis=sinkron").text
    assert "Dari Active Directory" in only and "Nadia Putri dibuat" not in only
    assert "Nadia Putri dibuat" in hc.get("/aktivitas?q=nadia").text
