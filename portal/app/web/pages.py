"""The activity feed and the system status page."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.ad_client import AdError
from app.domain.background import minutes_or_seconds
from app.domain.catalog import DEPARTMENTS, department_ou, department_pairs, department_parent, CatalogError
from app.domain.sync import sync_sources
from app.mail.mailer import MailError, SmtpMailer, smtp_config, smtp_problem
from app.web.context import Context


def _check(check_id: str, name: str, status: str, message: str) -> dict:
    return {"id": check_id, "name": name, "status": status, "message": message}


def ad_checks(ctx: Context, env) -> list[dict]:
    checks: list[dict] = []
    health = ctx.ad.health()
    if health is None:
        return [_check("service", "Layanan AD (Java)", "fail", "Layanan AD tidak menjawab. Periksa container hc-ad-service.")]
    directory = health.get("directory")
    if directory != "ldaps":
        status = "warn" if directory == "demo" else "fail"
        return [_check("driver", "Koneksi AD", status,
                       "Mode demo: akun demo, tanpa Active Directory." if directory == "demo"
                       else "AD_LDAP_URL/LDAP_URL belum diisi: belum ada Active Directory.")]
    checks.append(_check("driver", "Koneksi AD", "ok", "LDAPS port 636, sertifikat diverifikasi."))

    base = (env.get("AD_BASE_DN") or env.get("LDAP_BASE_DN") or "").strip()
    if not base:
        checks.append(_check("bind", "Akun layanan & base DN", "fail", "AD_BASE_DN belum diisi."))
        return checks
    try:
        exists = ctx.ad.entry_exists(base)
        checks.append(_check("bind", "Akun layanan & base DN", "ok" if exists else "fail",
                             f"Bind akun layanan berhasil; {base} {'ditemukan' if exists else 'TIDAK ditemukan'}."))
    except AdError as error:
        checks.append(_check("bind", "Akun layanan & base DN", "fail", str(error)))
        return checks

    managed = [part.strip() for part in (env.get("AD_MANAGED_OUS") or "").split(";") if part.strip()]
    missing = [ou for ou in managed if not ctx.ad.entry_exists(ou)]
    checks.append(_check("managed-ous", "OU yang dikelola", "fail" if missing or not managed else "ok",
                         "AD_MANAGED_OUS belum berisi OU." if not managed else
                         (f"Tidak ditemukan: {'; '.join(missing)}." if missing else f"{len(managed)} OU kelola ditemukan.")))

    quarantine = (env.get("AD_QUARANTINE_OU") or "").strip()
    if quarantine:
        found = ctx.ad.entry_exists(quarantine)
        checks.append(_check("quarantine-ou", "OU karantina", "ok" if found else "fail",
                             f"{quarantine} {'ditemukan' if found else 'tidak ditemukan'}."))
    else:
        checks.append(_check("quarantine-ou", "OU karantina", "fail", "AD_QUARANTINE_OU belum diisi."))

    try:
        pairs, parent = department_pairs(env), department_parent(env)
        if not pairs and not parent:
            checks.append(_check("department-ous", "OU per divisi", "skip",
                                 "AD_DEPARTMENT_OUS dan AD_DEPARTMENT_OU_PARENT kosong: semua akun baru masuk AD_OU_STANDARD."))
        else:
            named = {d.lower() for d, _ in pairs}
            targets = [(d, ou, True) for d, ou in pairs]
            if parent:
                targets += [(d, department_ou(d, env), False) for d in DEPARTMENTS if d.lower() not in named]
            absent = [(d, ou, explicit) for d, ou, explicit in targets if not ctx.ad.entry_exists(ou)]
            if any(explicit for _, _, explicit in absent):
                checks.append(_check("department-ous", "OU per divisi", "fail",
                                     "OU divisi tidak ditemukan: " + "; ".join(f"{d} ({ou})" for d, ou, e in absent if e) + "."))
            elif absent:
                checks.append(_check("department-ous", "OU per divisi", "warn",
                                     f"{len(targets) - len(absent)} OU divisi ditemukan; belum ada untuk: "
                                     + ", ".join(d for d, _, _ in absent) + "."))
            else:
                checks.append(_check("department-ous", "OU per divisi", "ok", f"{len(targets)} OU divisi ditemukan."))
    except CatalogError as error:
        checks.append(_check("department-ous", "OU per divisi", "fail", str(error)))

    group = (env.get("CISO_APPROVER_GROUP") or "").strip()
    if group:
        try:
            members = ctx.ad.group_members(group)
            checks.append(_check("ciso-group", "Group tim CISO", "ok" if members else "fail",
                                 f"{len(members)} anggota aktif yang punya email." if members
                                 else "Group tidak punya anggota aktif yang punya email."))
        except AdError as error:
            checks.append(_check("ciso-group", "Group tim CISO", "fail", str(error)))
    else:
        checks.append(_check("ciso-group", "Group tim CISO", "skip", "CISO_APPROVER_GROUP tidak diisi; dipakai CISO_APPROVER_EMAILS."))

    write = (env.get("AD_LDAP_WRITE_ENABLED") or "").strip().lower() == "true"
    checks.append(_check("write", "Mode tulis", "ok" if write else "warn",
                         "Penulisan LDAP diaktifkan." if write else "Mode baca: AD_LDAP_WRITE_ENABLED=false."))

    sync = ctx.background.get("sync_status") or {}
    if sync.get("report"):
        r = sync["report"]
        checks.append(_check("employee-sync", "Sinkron karyawan", "ok",
                             f"Terakhir {r['at'][:16].replace('T', ' ')} UTC: {r['read']} akun dibaca; {r['created']} baru, "
                             f"{r['updated']} diperbarui, {r['linked']} ditautkan."))
    elif sync.get("error"):
        checks.append(_check("employee-sync", "Sinkron karyawan", "fail", sync["error"]))
    else:
        checks.append(_check("employee-sync", "Sinkron karyawan", "skip",
                             f"Belum berjalan. Sumber: {'; '.join(sync_sources(env)) or '(kosong)'}."))
    return checks


def portal_checks(ctx: Context, env) -> list[dict]:
    settings = ctx.settings
    checks: list[dict] = []
    driver = (env.get("EMAIL_DRIVER") or "").strip().lower()
    redirect = (env.get("EMAIL_REDIRECT_TO") or "").strip()
    redirected = f" Semua email DIALIHKAN ke {redirect} (EMAIL_REDIRECT_TO) — kosongkan sebelum dipakai sungguhan." if redirect else ""
    if driver == "smtp":
        problem = smtp_problem(env)
        if problem:
            checks.append(_check("email", "Pengiriman email", "fail", problem))
        else:
            config = smtp_config(env)
            try:
                SmtpMailer(config).verify()
                login = f"login sebagai {config.user} berhasil" if config.user else "tanpa login (relay)"
                tls = " TANPA enkripsi (SMTP_TLS=off)." if config.tls == "off" else " dengan TLS."
                checks.append(_check("email", "Pengiriman email", "warn" if config.tls == "off" or redirect else "ok",
                                     f"SMTP {config.host}:{config.port} terhubung, {login}, pengirim {config.sender},{tls}{redirected}"))
            except MailError as error:
                checks.append(_check("email", "Pengiriman email", "fail", f"SMTP {config.host}:{config.port} gagal: {error}"))
    elif driver == "file":
        checks.append(_check("email", "Pengiriman email", "fail" if settings.production else "warn",
                             "EMAIL_DRIVER=file: email hanya ditulis ke folder data/outbox-mail, tidak ada yang terkirim."))
    else:
        checks.append(_check("email", "Pengiriman email", "fail", f"EMAIL_DRIVER=\"{driver}\" tidak bisa dipakai. Isi smtp."))

    poll = minutes_or_seconds(env.get("OUTBOX_POLL_SECONDS"), 30, "OUTBOX_POLL_SECONDS")
    stuck = ctx.background.get("mail_error")
    if stuck:
        checks.append(_check("outbox", "Antrean email", "fail", f"Antrean menunggu, belum ada yang bisa dikirim: {stuck}"))
    else:
        checks.append(_check("outbox", "Antrean email", "ok" if poll else "warn",
                             f"Email dikirim saat itu juga; antrean diperiksa ulang tiap {poll} detik untuk yang gagal." if poll
                             else "Pengiriman otomatis mati: email hanya terkirim bila dikirim dari halaman ini."))
    checks.append(_check("login-2fa", "Verifikasi 2 langkah", "ok" if settings.login_2fa else "warn",
                         f"Aktif: setiap login wajib kode dari aplikasi authenticator. {ctx.mfa.count()} orang sudah mendaftarkan HP."
                         if settings.login_2fa else "Dimatikan (LOGIN_2FA=off): login cukup dengan kata sandi AD."))
    demo = (env.get("DEMO_LOGIN") or "").strip().lower() in ("on", "true", "1", "yes") and not settings.production
    checks.append(_check("demo-login", "Akun demo", "warn" if demo else "ok",
                         "Akun demo aktif (DEMO_LOGIN=on): hanya untuk laptop, jangan untuk kantor." if demo else "Akun demo mati."))
    recheck = minutes_or_seconds(env.get("SESSION_RECHECK_MINUTES"), 5, "SESSION_RECHECK_MINUTES")
    checks.append(_check("session-recheck", "Cek ulang sesi ke AD", "ok" if recheck else "warn",
                         f"Sesi dicocokkan dengan AD tiap {recheck} menit: akun nonaktif atau keluar dari group HC otomatis keluar." if recheck
                         else "Dimatikan: akun yang dinonaktifkan di AD tetap bisa memakai portal sampai sesinya habis."))
    base = settings.app_base_url
    checks.append(_check("https", "HTTPS", "skip" if not settings.production else ("ok" if base.startswith("https://") else "fail"),
                         "Mode pengembangan: HTTPS tidak diwajibkan." if not settings.production else
                         (f"Portal dan link persetujuan memakai {base}." if base.startswith("https://") else
                          f'APP_BASE_URL="{base}" bukan https: cookie sesi aman tidak terkirim lewat jaringan, dan link persetujuan di email tidak terenkripsi.')))
    return checks


def register(app: FastAPI, ctx: Context) -> None:
    @app.get("/aktivitas", response_class=HTMLResponse)
    def activity(request: Request):
        session, refused = ctx.guard(request, "activity.read")
        if refused:
            return refused
        entries = sorted(ctx.store.read()["activity"], key=lambda e: e["at"], reverse=True)[:200]
        return ctx.page(request, "activity.html", session=session, entries=entries)

    @app.get("/status-ad", include_in_schema=False)
    def old_status_address():
        """The address the status page had before; bookmarks keep working."""
        return RedirectResponse("/status", status_code=308)

    @app.get("/status", response_class=HTMLResponse)
    def status(request: Request):
        session, refused = ctx.guard(request, "execution.run")
        if refused:
            return refused
        env = ctx.env
        data = ctx.store.read()
        outbox = {state: sum(1 for e in data["outboxEvents"] if e["state"] == state) for state in ("PENDING", "SENT", "DEAD")}
        return ctx.page(request, "status.html", session=session, ad=ad_checks(ctx, env), portal=portal_checks(ctx, env),
                        events=ctx.security.recent(40), outbox=outbox, message=request.query_params.get("pesan"))

    @app.post("/status/jalankan")
    async def run_now(request: Request):
        session, refused = ctx.guard(request, "execution.run")
        if refused:
            return refused
        what = (await request.form()).get("apa", "")
        loop = {"worker": "worker", "email": "outbox", "sinkron": "sync"}.get(str(what))
        if loop:
            ctx.background[loop].run_now()
        return RedirectResponse("/status?pesan=Dijalankan. Muat ulang halaman ini sebentar lagi untuk melihat hasilnya.", status_code=303)
