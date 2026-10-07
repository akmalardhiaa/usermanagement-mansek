"""The portal: plain HTML pages rendered on the server, for HC.

The directory, lifecycle requests and their email approvals, the worker that
carries approved requests out in Active Directory (through the Java AD
service), the sync that fills the directory from AD, and sign-in with two
steps. One process: the stores lock per process, and the background loops
run in it.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Mapping

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.ad_client import AdClient
from app.domain.background import Loop, minutes_or_seconds
from app.domain.lifecycle import Lifecycle
from app.domain.outbox import Dispatcher, Emitter, Sealer
from app.domain.recheck import recheck_sessions
from app.domain.store import Store, seed_demo_roster
from app.domain.sync import describe, sync_employees
from app.domain.worker import Worker
from app.mail.mailer import mailer_from_env
from app.security.mfa_store import MfaStore
from app.security.pending import PendingLogins
from app.security.security_log import SecurityLog
from app.security.sessions import SessionStore
from app.security.throttle import Throttle
from app.security.web import ProtectionMiddleware
from app.settings import Settings
from app.web import approvals, auth, directory, pages, requests
from app.web.context import Context

HERE = Path(__file__).parent
logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per AD call drowns the log
log = logging.getLogger("portal")


def _directory_configured(ad: AdClient) -> bool:
    health = ad.health()
    return bool(health and health.get("directory") == "ldaps")


def create_app(settings: Settings | None = None, ad: AdClient | None = None, env: Mapping[str, str] | None = None,
               start_background: bool = True) -> FastAPI:
    env = os.environ if env is None else env
    settings = settings or Settings.from_env(env)
    ad = ad or AdClient(settings.ad_service_url, settings.ad_service_token)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(ProtectionMiddleware, app_base_url=settings.app_base_url, production=settings.production)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    store_file = (env.get("HC_DATA_FILE") or "").strip()
    store = Store(Path(store_file) if store_file else settings.data_dir / "hc-store.json")
    sealer = Sealer(settings.encryption_key)
    ttl = int((env.get("APPROVAL_TOKEN_TTL_HOURS") or "24").strip() or 24)
    lifecycle = Lifecycle(store, Emitter(sealer, ttl), env, group_members=ad.group_members)
    dispatcher = Dispatcher(store, sealer, lambda: mailer_from_env(env, settings.data_dir), settings.app_base_url)
    worker = Worker(store, ad, env)
    sessions = SessionStore(settings.session_file, settings.session_idle_minutes, settings.session_absolute_minutes)
    security = SecurityLog(settings.security_log_file)
    background: dict = {"sync_status": {}, "worker_waiting": False}

    def send_mail() -> None:
        report = dispatcher.dispatch_due()
        if report["error"]:
            if not background.get("mail_error"):
                log.error("[email] Email belum bisa dikirim, antrean menunggu: %s", report["error"])
            background["mail_error"] = report["error"]
            return
        background["mail_error"] = None
        if report["attempted"]:
            log.info("[email] %s terkirim, %s akan diulang, %s gagal permanen.", report["sent"], report["retried"], report["dead"])

    def run_worker() -> None:
        if not _directory_configured(ad):
            if not background["worker_waiting"]:
                log.warning("[worker] Worker menunggu: Active Directory belum dikonfigurasi. Portal tetap berjalan, "
                            "dan pengajuan yang sudah disetujui menunggu di antrean.")
                background["worker_waiting"] = True
            return
        background["worker_waiting"] = False
        report = worker.run_due()
        if report["ran"] or report["reclaimed"]:
            log.info("[worker] %s dijalankan: %s selesai, %s gagal.", report["ran"], report["completed"], report["failed"])

    def run_sync() -> None:
        if not _directory_configured(ad):
            return
        try:
            report = sync_employees(store, ad, env)
            background["sync_status"] = {"report": report}
            log.info("[ad-sync] %s akun dibaca dari AD: %s.", report["read"], describe(report))
        except Exception as error:
            background["sync_status"] = {"error": f"Sinkronisasi gagal: {error}"}
            raise

    def run_recheck() -> None:
        if not _directory_configured(ad):
            return
        report = recheck_sessions(sessions, ad, security)
        if report["error"]:
            log.warning("[session-recheck] AD tidak terbaca, sesi dibiarkan sampai putaran berikutnya: %s", report["error"])
        for revoked in report["revoked"]:
            log.info("[session-recheck] sesi %s diakhiri: %s", revoked["username"], revoked["reason"])

    outbox_seconds = minutes_or_seconds(env.get("OUTBOX_POLL_SECONDS"), 30, "OUTBOX_POLL_SECONDS")
    worker_seconds = minutes_or_seconds(env.get("WORKER_POLL_SECONDS"), 30, "WORKER_POLL_SECONDS")
    sync_minutes = minutes_or_seconds(env.get("AD_SYNC_MINUTES"), 15, "AD_SYNC_MINUTES")
    recheck_minutes = minutes_or_seconds(env.get("SESSION_RECHECK_MINUTES"), 5, "SESSION_RECHECK_MINUTES")
    background.update(
        outbox=Loop("email", outbox_seconds or 3600, send_mail, first_delay=1),
        worker=Loop("worker", worker_seconds or 3600, run_worker, first_delay=2),
        sync=Loop("ad-sync", (sync_minutes or 60) * 60, run_sync, first_delay=5),
        recheck=Loop("session-recheck", (recheck_minutes or 60) * 60, run_recheck, first_delay=60),
    )
    store.on_mail_queued(background["outbox"].kick)

    ctx = Context(settings=settings, ad=ad, store=store, sessions=sessions, mfa=MfaStore(settings.mfa_file, settings.encryption_key),
                  pending=PendingLogins(), throttle=Throttle(), security=security, lifecycle=lifecycle,
                  dispatcher=dispatcher, worker=worker, background=background, env=env)
    app.state.ctx = ctx
    # Kept for tests written against phase 1.
    app.state.settings, app.state.sessions, app.state.mfa = settings, sessions, ctx.mfa
    app.state.pending, app.state.throttle, app.state.security = ctx.pending, ctx.throttle, security

    demo = (env.get("DEMO_LOGIN") or "").strip().lower() in ("on", "true", "1", "yes")
    if demo and not settings.production and not (env.get("AD_LDAP_URL") or env.get("LDAP_URL") or "").strip():
        seed_demo_roster(store)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> PlainTextResponse:
        return PlainTextResponse("ok")

    for module in (auth, directory, requests, approvals, pages):
        module.register(app, ctx)

    if start_background:
        def start() -> None:
            for name, enabled in (("outbox", outbox_seconds), ("worker", worker_seconds), ("sync", sync_minutes),
                                  ("recheck", recheck_minutes)):
                if enabled:
                    background[name].start()
            log.info("[portal] email tiap %ss (dan saat itu juga), worker tiap %ss, sinkron AD tiap %s menit, "
                     "cek sesi tiap %s menit.", outbox_seconds, worker_seconds, sync_minutes, recheck_minutes)

        app.add_event_handler("startup", start)

    return app


def app_factory() -> FastAPI:
    """What the container runs: `uvicorn --factory app.main:app_factory`."""
    return create_app()
