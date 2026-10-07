"""Signing in end to end: password, two-step verification, sessions, limits."""

import time

from app.security import totp
from app.security.pending import PENDING_COOKIE
from app.security.sessions import SESSION_COOKIE

from conftest import ORIGIN


def password(client, username="admin", secret_word="admin12345", **headers):
    return client.post("/login", data={"username": username, "password": secret_word}, headers={**ORIGIN, **headers})


def code(client, value):
    return client.post("/login/verify", data={"code": value}, headers=ORIGIN)


def current_code(secret):
    return totp.code_for(secret, totp.step_at(time.time()))


def enroll(client):
    """First sign-in through to a session; returns the secret the QR carried."""
    assert password(client).status_code == 303
    secret = client.app.state.pending._items[next(iter(client.app.state.pending._items))].secret
    response = code(client, current_code(secret))
    assert response.status_code == 303 and response.headers["location"] == "/"
    client.cookies.clear()
    return secret


def test_first_sign_in_shows_a_qr_and_opens_no_session_on_the_password_alone(client):
    response = password(client)
    assert response.status_code == 303 and response.headers["location"] == "/login/verify"
    assert SESSION_COOKIE not in client.cookies
    assert PENDING_COOKIE in client.cookies

    page = client.get("/login/verify")
    assert page.status_code == 200
    assert "data:image/svg+xml" in page.text
    assert "Aktifkan verifikasi 2 langkah" in page.text
    # Without a session the home page is out of reach.
    assert client.get("/").headers["location"] == "/login"


def test_the_phone_is_stored_only_once_a_code_from_it_is_right(client):
    password(client)
    pending = client.app.state.pending
    secret = next(iter(pending._items.values())).secret

    wrong = code(client, "000000")
    assert wrong.status_code == 401 and "Kode salah" in wrong.text
    assert client.app.state.mfa.count() == 0

    right = code(client, current_code(secret))
    assert right.status_code == 303
    assert SESSION_COOKIE in client.cookies
    assert client.app.state.mfa.count() == 1
    assert "Direktori Karyawan" in client.get("/").text


def test_the_secret_is_sealed_on_disk(client, tmp_path):
    secret = enroll(client)
    assert secret not in (tmp_path / "hc-mfa.json").read_text()


def test_a_connected_phone_is_asked_for_a_code_only(client):
    enroll(client)
    password(client)
    page = client.get("/login/verify")
    assert "Verifikasi 2 langkah" in page.text and "data:image/svg+xml" not in page.text


def test_a_code_is_refused_a_second_time(client, monkeypatch):
    secret = enroll(client)
    later = time.time() + 30
    monkeypatch.setattr(totp.time, "time", lambda: later)

    password(client)
    used = totp.code_for(secret, totp.step_at(later))
    assert code(client, used).status_code == 303

    client.cookies.clear()
    password(client)
    again = code(client, used)
    assert again.status_code == 401 and "sudah dipakai" in again.text


def test_five_wrong_codes_lock_the_account(client):
    enroll(client)
    password(client)
    for _ in range(4):
        assert code(client, "111111").status_code == 401
    last = code(client, "111111")
    assert last.status_code == 429 and "dikunci" in last.text
    assert PENDING_COOKIE not in client.cookies


def test_five_wrong_passwords_lock_the_account_before_ad_is_asked(client, ad):
    for _ in range(4):
        assert password(client, secret_word="salah").status_code == 401
    assert password(client, secret_word="salah").status_code == 429
    calls = ad.calls
    locked = password(client)
    assert locked.status_code == 429
    assert ad.calls == calls  # AD was not asked while locked


def test_a_made_up_forwarded_address_does_not_reset_the_account_lock(client):
    for attempt in range(5):
        password(client, secret_word="salah", **{"X-Forwarded-For": f"203.0.113.{attempt}"})
    assert password(client, **{"X-Forwarded-For": "198.51.100.7"}).status_code == 429


def test_a_manager_with_a_correct_password_is_sent_to_the_email(client):
    response = password(client, "bagus", "bagus12345")
    assert response.status_code == 403
    assert "Portal ini hanya untuk tim Human Capital" in response.text


def test_an_unreachable_directory_is_told_to_call_it(client, ad):
    ad.down = True
    response = password(client)
    assert response.status_code == 503 and "Hubungi tim IT" in response.text


def test_login_2fa_off_opens_the_session_on_the_password(make_client):
    client = make_client(LOGIN_2FA="off")
    response = password(client)
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert SESSION_COOKIE in client.cookies


def test_logout_ends_the_session_on_the_server(client, tmp_path):
    enroll(client)
    password(client)
    secret_page = client.app.state.mfa.read("admin")
    later = time.time()
    code(client, totp.code_for(secret_page.secret, totp.step_at(later) + 1))
    session_id = client.cookies.get(SESSION_COOKIE)
    assert client.app.state.sessions.resolve(session_id) is not None

    response = client.post("/logout", headers=ORIGIN)
    assert response.status_code == 303
    assert client.app.state.sessions.resolve(session_id) is None


def test_every_attempt_is_logged_without_the_password(client, tmp_path):
    password(client, secret_word="rahasia-yang-salah")
    events = [event["type"] for event in client.app.state.security.recent()]
    assert events == ["login.failed"]
    assert "rahasia-yang-salah" not in (tmp_path / "hc-security-log.jsonl").read_text()
