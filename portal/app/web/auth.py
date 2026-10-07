"""Signing in and out: AD password, then the authenticator code."""

from __future__ import annotations

import segno
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.security import totp
from app.security.mfa_store import MfaUnreadableError
from app.security.pending import MAX_ATTEMPTS, PENDING_COOKIE, TTL_SECONDS
from app.security.sessions import SESSION_COOKIE
from app.security.throttle import LOCK_MINUTES
from app.security.web import client_address
from app.web.context import Context


def register(app: FastAPI, ctx: Context) -> None:
    settings, sessions, mfa, pending, throttle, security, ad = (
        ctx.settings, ctx.sessions, ctx.mfa, ctx.pending, ctx.throttle, ctx.security, ctx.ad)

    def set_cookie(response: Response, key: str, value: str, max_age: int, path: str, same_site: str) -> None:
        response.set_cookie(key, value, max_age=max_age, path=path, secure=settings.cookies_secure,
                            httponly=True, samesite=same_site)

    def clear_cookie(response: Response, key: str, path: str) -> None:
        response.delete_cookie(key, path=path, secure=settings.cookies_secure, httponly=True)

    def demo_mode() -> bool:
        health = ad.health()
        return bool(health and health.get("directory") == "demo")

    def login_page(request: Request, status: int, error: str | None, username: str = "") -> HTMLResponse:
        return ctx.page(request, "login.html", status=status, error=error, username=username, demo=demo_mode())

    def sign_in(user: dict, ip: str, via_mfa: bool) -> RedirectResponse:
        session_id = sessions.create(user)
        throttle.clear(user["username"])
        security.record("login.success", user["username"], ip, "dengan 2FA" if via_mfa else "tanpa 2FA (LOGIN_2FA=off)")
        response = RedirectResponse("/", status_code=303)
        set_cookie(response, SESSION_COOKIE, session_id, sessions.max_age, "/", "lax")
        clear_cookie(response, PENDING_COOKIE, "/login")
        return response

    def locked_message(seconds: int) -> str:
        minutes = max(1, -(-seconds // 60))
        return f"Terlalu banyak percobaan gagal untuk akun ini. Akun dikunci sementara di portal; coba lagi dalam {minutes} menit."

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        if ctx.session(request):
            return RedirectResponse("/", status_code=303)
        return login_page(request, 200, None)

    @app.post("/login", response_class=HTMLResponse)
    def login(request: Request, username: str = Form(""), password: str = Form("")):
        ip = client_address(request)
        if not throttle.allow_address(ip):
            return login_page(request, 429, "Terlalu banyak percobaan masuk. Coba lagi sebentar lagi.", username)
        username = username.strip()
        if not username or not password:
            return login_page(request, 422, "Username dan kata sandi wajib diisi.", username)

        # Before AD is asked: a locked account must not add to the domain's own lockout count.
        locked = throttle.locked_for(username)
        if locked:
            security.record("login.locked", username, ip, "ditolak selama terkunci")
            return login_page(request, 429, locked_message(locked), username)

        result = ad.login(username, password)
        if result.code == "INVALID_CREDENTIALS":
            now_locked = throttle.fail(username)
            security.record("login.locked" if now_locked else "login.failed", username, ip,
                            f"kata sandi salah; akun dikunci {LOCK_MINUTES} menit" if now_locked else "kata sandi salah")
            message = locked_message(LOCK_MINUTES * 60) if now_locked else "Username atau kata sandi salah."
            return login_page(request, 429 if now_locked else 401, message, username)
        if result.user is None:
            security.record("login.unavailable", username, ip, result.message)
            message = (result.message if result.code == "NOT_CONFIGURED"
                       else "Server Active Directory tidak bisa dihubungi. Hubungi tim IT.")
            return login_page(request, 503, message, username)

        user = result.user
        # Only HC signs in here. Managers and the CISO team approve from their email.
        if not user.get("roles"):
            security.record("login.denied", user["username"], ip, "bukan anggota group portal")
            return login_page(request, 403, "Portal ini hanya untuk tim Human Capital. Manager dan tim CISO memberikan "
                                             "persetujuan langsung dari email yang mereka terima.", username)

        throttle.reset_address(ip)
        if not settings.login_2fa:
            return sign_in(user, ip, False)
        try:
            enrolled = mfa.read(user["id"]) is not None
        except MfaUnreadableError:
            security.record("mfa.failed", user["username"], ip, "kunci 2FA tidak bisa dibuka")
            return login_page(request, 409, "Verifikasi 2 langkah akun ini perlu diatur ulang. Hubungi admin portal.", username)
        step_id = pending.begin(user, "verify" if enrolled else "enroll", None if enrolled else totp.generate_secret())
        response = RedirectResponse("/login/verify", status_code=303)
        set_cookie(response, PENDING_COOKIE, step_id, TTL_SECONDS, "/login", "strict")
        return response

    def verify_page(request: Request, step, status: int, error: str | None) -> HTMLResponse:
        context: dict = {"mode": step.mode, "error": error, "account": step.user["username"]}
        if step.mode == "enroll":
            uri = totp.otpauth_uri(settings.mfa_issuer, step.user["username"], step.secret)
            context["qr"] = segno.make(uri, error="m").svg_data_uri(scale=5, border=2, dark="#071321", light="#ffffff")
            context["secret"] = " ".join(step.secret[i:i + 4] for i in range(0, len(step.secret), 4))
            context["issuer"] = settings.mfa_issuer
        return ctx.page(request, "verify.html", status=status, **context)

    @app.get("/login/verify", response_class=HTMLResponse)
    def verify_form(request: Request):
        step = pending.find(request.cookies.get(PENDING_COOKIE))
        if step is None:
            return RedirectResponse("/login", status_code=303)
        return verify_page(request, step, 200, None)

    @app.post("/login/verify", response_class=HTMLResponse)
    def verify(request: Request, code: str = Form("")):
        ip = client_address(request)
        step_id = request.cookies.get(PENDING_COOKIE)
        step = pending.find(step_id)
        if step is None:
            response = login_page(request, 401, "Langkah verifikasi sudah kedaluwarsa. Silakan masuk lagi.")
            clear_cookie(response, PENDING_COOKIE, "/login")
            return response

        user = step.user
        locked = throttle.locked_for(user["username"])
        if locked:
            pending.end(step_id)
            response = login_page(request, 429, locked_message(locked))
            clear_cookie(response, PENDING_COOKIE, "/login")
            return response

        accepted = reused = False
        try:
            if step.mode == "enroll":
                matched = totp.match(step.secret, code)
                if matched is not None:
                    accepted = mfa.save(user["id"], step.secret, matched)
                    if accepted:
                        security.record("mfa.enrolled", user["username"], ip, "authenticator terdaftar")
            else:
                enrollment = mfa.read(user["id"])
                matched = totp.match(enrollment.secret, code) if enrollment else None
                if matched is not None:
                    accepted = mfa.consume_step(user["id"], matched)
                    reused = not accepted
        except MfaUnreadableError:
            pending.end(step_id)
            response = login_page(request, 409, "Verifikasi 2 langkah akun ini perlu diatur ulang. Hubungi admin portal.")
            clear_cookie(response, PENDING_COOKIE, "/login")
            return response

        if accepted:
            pending.end(step_id)
            return sign_in(user, ip, True)

        exhausted = pending.fail(step_id)
        now_locked = throttle.fail(user["username"])
        security.record("login.locked" if now_locked else "mfa.failed", user["username"], ip,
                        "kode sudah pernah dipakai" if reused else
                        (f"kode 2FA salah; akun dikunci {LOCK_MINUTES} menit" if now_locked else "kode 2FA salah"))
        if exhausted or now_locked:
            pending.end(step_id)
            message = locked_message(LOCK_MINUTES * 60) if now_locked else "Terlalu banyak kode salah. Silakan masuk lagi dari awal."
            response = login_page(request, 429 if now_locked else 401, message)
            clear_cookie(response, PENDING_COOKIE, "/login")
            return response
        message = ("Kode ini sudah dipakai. Tunggu kode berikutnya muncul di aplikasi, lalu coba lagi." if reused
                   else "Kode salah. Pastikan jam di HP sudah otomatis, lalu masukkan kode yang sedang tampil.")
        return verify_page(request, step, 401, f"{message} (sisa {MAX_ATTEMPTS - step.attempts} percobaan)")

    @app.post("/logout")
    def logout(request: Request):
        session_id = request.cookies.get(SESSION_COOKIE)
        session = sessions.resolve(session_id)
        sessions.revoke(session_id)
        if session:
            security.record("logout", session.username, client_address(request))
        response = RedirectResponse("/login", status_code=303)
        clear_cookie(response, SESSION_COOKIE, "/")
        return response
