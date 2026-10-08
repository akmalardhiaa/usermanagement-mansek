"""Every page, as every role, in every request state: no errors, no dead links, sound HTML.

The office will click through screens nobody wrote a test for. This walks the
whole portal the way a person would — following every link it draws — over a
store holding a request in each state, and fails on a server error, a link to
nowhere, a form posting to a route that does not exist, or broken markup.
"""

from __future__ import annotations

import json
import re
from collections import deque
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.domain.sync import sync_employees
from app.main import create_app
from app.settings import Settings

from conftest import ORIGIN, env_for
from fake_directory import DIRECTORY_ENV, PEOPLE, FakeDirectory

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr",
        "path", "circle", "rect", "polyline"}
# Elements the HTML parser lets close implicitly; their end tags are optional.
OPTIONAL_END = {"p", "li", "option", "optgroup", "tr", "td", "th", "thead", "tbody", "dt", "dd"}


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.forms: list[tuple[str, str]] = []
        self.problems: list[str] = []
        self.stack: list[str] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            if attrs["id"] in self.ids:
                self.problems.append(f"duplicate id {attrs['id']}")
            self.ids.add(attrs["id"])
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if tag == "form":
            self.forms.append(((attrs.get("method") or "get").lower(), attrs.get("action") or ""))
        if tag in ("img",) and not attrs.get("alt") and attrs.get("alt") != "":
            self.problems.append("img without alt")
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.stack and self.stack[-1] == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        while self.stack and self.stack[-1] != tag and self.stack[-1] in OPTIONAL_END:
            self.stack.pop()
        if not self.stack or self.stack[-1] != tag:
            self.problems.append(f"unexpected </{tag}> (open: {self.stack[-3:]})")
            return
        self.stack.pop()

    def close(self):
        super().close()
        leftover = [t for t in self.stack if t not in OPTIONAL_END]
        if leftover:
            self.problems.append(f"never closed: {leftover}")


def routes(app) -> list[tuple[set[str], re.Pattern]]:
    out = []
    for route in app.routes:
        methods = getattr(route, "methods", None) or {"GET"}
        pattern = re.sub(r"\{[^}:]+(:path)?\}", lambda m: ".+" if m.group(1) else "[^/]+", route.path)
        out.append((set(methods), re.compile(f"^{pattern}$")))
    return out


@pytest.fixture
def portal(tmp_path):
    directory = FakeDirectory()
    for name, display, department, manager in [
        ("ayu.prameswari", "Ayu Prameswari", "Human Capital", "dimas.anggara"),
        ("dimas.anggara", "Dimas Anggara", "Human Capital", ""),
        ("sarah.wijaya", "Sarah Wijaya", "IT — Engineering", "dimas.anggara"),
        ("rizky.maulana", "Rizky Maulana", "IT — Engineering", "sarah.wijaya"),
        ("dewi.lestari", "Dewi Lestari", "Finance", "dimas.anggara"),
        ("budi.santoso", "Budi Santoso", "Finance", "dimas.anggara"),
        ("tono.saputra", "Tono Saputra", "Operations", "dimas.anggara"),
    ]:
        directory.add(name, display, f"{name}@example.com", department, f"OU={department},{PEOPLE}", manager=manager)
    directory.add("lama.keluar", "Lama Keluar", "lama.keluar@example.com", "Finance", f"OU=Finance,{PEOPLE}", enabled=False)
    env = env_for(tmp_path, LOGIN_2FA="off", DEMO_LOGIN="off", **DIRECTORY_ENV)
    app = create_app(Settings.from_env(env), directory, env=env, start_background=False)
    ctx = app.state.ctx
    sync_employees(ctx.store, directory, env)
    people = {e["displayName"]: e for e in ctx.store.read()["employees"]}
    hc = TestClient(app, base_url="http://testserver", follow_redirects=False)
    hc.post("/login", data={"username": "hc", "password": "hc12345678"}, headers=ORIGIN)

    def raise_request(form):
        response = hc.post("/pengajuan/baru", data=form, headers=ORIGIN)
        assert response.status_code == 303, response.text[:300]
        return response.headers["location"].split("/pengajuan/")[1].split("?")[0]

    def onboarding(first, last):
        return raise_request({"jenis": "onboarding", "firstName": first, "lastName": last,
                              "displayName": f"{first} {last}", "email": f"{first}.{last}@example.com".lower(),
                              "userId": f"{first}.{last}".lower(), "jobTitle": "Analyst", "department": "Finance",
                              "managerEmail": "dimas.anggara@example.com", "employmentType": "PERMANENT",
                              "locationType": "PUSAT"})

    def mutation(name, department):
        return raise_request({"jenis": "mutasi", "employeeId": people[name]["id"], "toDepartment": department,
                              "toJobTitle": "Analyst", "toManagerEmail": "dimas.anggara@example.com"})

    def decide(request_id, stage_decisions):
        """Approve or reject through the email tokens, as the approvers would."""
        for decision in stage_decisions:
            ctx.dispatcher.dispatch_due()
            mails = sorted(Path(env["EMAIL_FILE_DIR"]).glob("*.html"), key=lambda p: p.stat().st_mtime)
            for mail in reversed(mails):
                match = re.search(r"/persetujuan/([^\"?]+)\?putusan=setuju", mail.read_text(encoding="utf-8"))
                if match and ctx.lifecycle.preview_by_token(match.group(1)).get("ok") and \
                        ctx.lifecycle.preview_by_token(match.group(1))["request"]["id"] == request_id:
                    ctx.lifecycle.decide_by_token(match.group(1), decision)
                    break

    waiting = onboarding("Nadia", "Putri")                     # PENDING_MANAGER
    at_ciso = onboarding("Bima", "Sakti")                      # PENDING_CISO
    decide(at_ciso, ["APPROVED"])
    done = onboarding("Citra", "Ayu")                          # COMPLETED
    decide(done, ["APPROVED", "APPROVED"])
    ctx.worker.run_due()
    rejected = mutation("Budi Santoso", "Legal")               # REJECTED
    decide(rejected, ["REJECTED"])
    failed = mutation("Rizky Maulana", "Risk Management")      # FAILED
    decide(failed, ["APPROVED", "APPROVED"])
    directory.fail_on = "attributes"
    ctx.worker.run_due()
    cancelled = mutation("Tono Saputra", "Legal")              # CANCELLED
    hc.post(f"/pengajuan/{cancelled}/batal", headers=ORIGIN)
    queued = mutation("Dewi Lestari", "Treasury")              # QUEUED (not run yet)
    decide(queued, ["APPROVED", "APPROVED"])
    states = {r["id"]: r["status"] for r in ctx.store.read()["lifecycleRequests"]}
    assert states[waiting] == "PENDING_MANAGER" and states[at_ciso] == "PENDING_CISO"
    assert states[done] == "COMPLETED" and states[rejected] == "REJECTED" and states[failed] == "FAILED"
    assert states[cancelled] == "CANCELLED" and states[queued] == "QUEUED"
    hc.get("/direktori/export.xlsx")  # an export entry in the activity feed
    return app, ctx, env


def crawl(client: TestClient, app, start: list[str]) -> tuple[dict[str, int], list[str]]:
    known = routes(app)
    seen: dict[str, int] = {}
    problems: list[str] = []
    queue = deque(start)
    while queue:
        url = queue.popleft()
        if url in seen or len(seen) > 400:
            continue
        response = client.get(url)
        seen[url] = response.status_code
        if response.status_code >= 500:
            problems.append(f"{url}: {response.status_code}")
            continue
        if "text/html" not in response.headers.get("content-type", ""):
            continue
        page = Page()
        page.feed(response.text)
        page.close()
        problems += [f"{url}: {p}" for p in page.problems]
        for method, action in page.forms:
            target = urlsplit(urljoin(url, action or url)).path
            if not any(method.upper() in methods and pattern.match(target) for methods, pattern in known):
                problems.append(f"{url}: form {method} {target} has no route")
        for href in page.links:
            parts = urlsplit(urljoin("http://testserver" + url, href))
            if parts.netloc != "testserver":
                continue
            link = parts.path + (f"?{parts.query}" if parts.query else "")
            if link.startswith("/static/"):
                if client.get(link).status_code != 200:
                    problems.append(f"{url}: static {link} missing")
                continue
            queue.append(link)
    return seen, problems


@pytest.mark.parametrize("username,password", [("hc", "hc12345678"), ("ops", "ops1234567"), ("admin", "admin12345")])
def test_every_page_a_role_can_reach_works(portal, username, password):
    app, ctx, env = portal
    client = TestClient(app, base_url="http://testserver", follow_redirects=False)
    assert client.post("/login", data={"username": username, "password": password}, headers=ORIGIN).status_code == 303
    start = ["/", "/pengajuan", "/pengajuan?status=semua", "/pengajuan/baru", "/aktivitas", "/status", "/?status=nonaktif",
             "/?status=pengajuan", "/?q=zzz-tidak-ada", "/aktivitas?q=zzz"]
    start += [f"/pengajuan/baru?jenis={slug}" for slug in ("onboarding", "mutasi", "resign", "profil")]
    start += [f"/pengajuan/{r['id']}" for r in ctx.store.read()["lifecycleRequests"]]
    seen, problems = crawl(client, app, start)
    assert not problems, "\n".join(problems)
    assert len(seen) > 25
    allowed = {200, 303, 403, 404, 308}
    assert all(code in allowed for code in seen.values()), {u: c for u, c in seen.items() if c not in allowed}
    # A link the page drew must not lead to "not found".
    assert not [u for u, c in seen.items() if c == 404], [u for u, c in seen.items() if c == 404]


def test_the_pages_an_approver_sees_from_email(portal):
    app, ctx, env = portal
    ctx.dispatcher.dispatch_due()
    anonymous = TestClient(app, base_url="http://testserver", follow_redirects=False)
    problems = []
    for mail in Path(env["EMAIL_FILE_DIR"]).glob("*.html"):
        for link in re.findall(r'href="http://testserver(/[^"]+)"', mail.read_text(encoding="utf-8")):
            link = link.replace("&amp;", "&")
            response = anonymous.get(link)
            if response.status_code == 303:  # the result email links into the portal: sign-in first
                assert response.headers["location"] == "/login"
                continue
            assert response.status_code == 200, (link, response.status_code)
            page = Page()
            page.feed(response.text)
            page.close()
            problems += [f"{link}: {p}" for p in page.problems]
    assert not problems, "\n".join(problems)


def test_the_portal_stays_quick_with_a_real_sized_directory(portal):
    """A thousand people and three hundred requests: pages under half a second, the export under two."""
    app, ctx, env = portal
    now = datetime.now(timezone.utc)

    def grow(data):
        template = data["employees"][0]
        for i in range(1000):
            data["employees"].append({**template, "id": f"emp_bulk_{i}", "displayName": f"Karyawan {i:04d}",
                                      "email": f"karyawan{i}@example.com", "objectGUID": None,
                                      "createdAt": (now - timedelta(days=i)).isoformat()})
        sample = data["lifecycleRequests"][0]
        for i in range(300):
            data["lifecycleRequests"].append(json.loads(json.dumps({**sample, "id": f"lr_bulk_{i}", "employeeId": None})))
    ctx.store.mutate(grow)
    client = TestClient(app, base_url="http://testserver", follow_redirects=False)
    client.post("/login", data={"username": "hc", "password": "hc12345678"}, headers=ORIGIN)
    import time
    for path, limit in (("/", 0.5), ("/?q=karyawan&urut=nama", 0.5), ("/pengajuan?status=semua", 0.5),
                        ("/aktivitas", 0.5), ("/karyawan/emp_bulk_7", 0.5), ("/direktori/export.xlsx", 2.0)):
        start = time.perf_counter()
        assert client.get(path).status_code == 200
        took = time.perf_counter() - start
        print(f"{path}: {took * 1000:.0f} ms")
        assert took < limit, path
