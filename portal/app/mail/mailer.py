"""Sending email: SMTP or Microsoft Graph for the office, files for a laptop demo.

SMTP covers both shapes an office has: a mailbox with a login (Microsoft 365,
Gmail with an App Password) and a company relay that accepts this host
without one. TLS is required by default — STARTTLS on 587/25, implicit on 465
— so a stripped STARTTLS offer stops the send instead of handing the password
over in the clear. SMTP_TLS=off is only for a relay with no TLS at all, and is
refused together with a login.

Microsoft Graph (EMAIL_DRIVER=graph) is for a Microsoft 365 tenant that has
switched SMTP logins off: an app registration with the Mail.Send application
permission sends from one mailbox, GRAPH_SENDER_MAILBOX.
"""

from __future__ import annotations

import json
import os
import smtplib
import socket
import ssl
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Mapping, Protocol
from urllib.parse import quote

import httpx


class MailError(Exception):
    """kind: TRANSIENT (retry), PERMISSION, RECIPIENT_REJECTED, PAYLOAD, CONFIG, UNKNOWN."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind

    @property
    def retryable(self) -> bool:
        return self.kind == "TRANSIENT"


@dataclass(frozen=True)
class Mail:
    to: str
    to_name: str | None
    subject: str
    html: str


class Mailer(Protocol):
    name: str

    def send(self, mail: Mail) -> str: ...


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    sender: str
    tls: str  # "required" | "off"
    ca_path: str | None


def _trim(env: Mapping[str, str], name: str) -> str:
    return (env.get(name) or "").strip()


def smtp_problem(env: Mapping[str, str]) -> str | None:
    """What is missing or contradictory in the SMTP settings, if anything."""
    missing = []
    if not _trim(env, "SMTP_HOST"):
        missing.append("SMTP_HOST")
    user, password = _trim(env, "SMTP_USER"), _trim(env, "SMTP_PASSWORD")
    if user and not password:
        missing.append("SMTP_PASSWORD")
    if password and not user:
        missing.append("SMTP_USER")
    if not _trim(env, "SMTP_SENDER") and not user:
        missing.append("SMTP_SENDER")
    if missing:
        return f"SMTP belum lengkap: {', '.join(missing)}."
    tls = _trim(env, "SMTP_TLS").lower()
    if tls not in ("", "required", "on", "true", "off", "none", "false"):
        return f'SMTP_TLS="{_trim(env, "SMTP_TLS")}" tidak dikenal. Isi required (bawaan) atau off.'
    if tls in ("off", "none", "false") and user:
        return "SMTP_TLS=off ditolak bila SMTP_USER/SMTP_PASSWORD diisi: kata sandi tidak boleh lewat koneksi tanpa enkripsi."
    return None


def smtp_config(env: Mapping[str, str]) -> SmtpConfig:
    problem = smtp_problem(env)
    if problem:
        raise MailError("CONFIG", problem)
    port_text = _trim(env, "SMTP_PORT")
    tls = _trim(env, "SMTP_TLS").lower()
    return SmtpConfig(
        host=_trim(env, "SMTP_HOST"),
        port=int(port_text) if port_text.isdigit() else 587,
        user=_trim(env, "SMTP_USER"),
        password=env.get("SMTP_PASSWORD") or "",
        sender=_trim(env, "SMTP_SENDER") or _trim(env, "SMTP_USER"),
        tls="off" if tls in ("off", "none", "false") else "required",
        ca_path=_trim(env, "SMTP_CA_CERT_PATH") or None,
    )


class SmtpMailer:
    name = "smtp"

    def __init__(self, config: SmtpConfig):
        self.config = config

    def _context(self) -> ssl.SSLContext:
        try:
            context = ssl.create_default_context(cafile=self.config.ca_path)
        except (OSError, ssl.SSLError) as error:
            raise MailError("CONFIG", f'Sertifikat CA di SMTP_CA_CERT_PATH="{self.config.ca_path}" tidak bisa dibaca: {error}') from error
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        return context

    def _open(self) -> smtplib.SMTP:
        config = self.config
        if config.port == 465:
            server: smtplib.SMTP = smtplib.SMTP_SSL(config.host, config.port, context=self._context(), timeout=15)
        else:
            server = smtplib.SMTP(config.host, config.port, timeout=15)
            server.ehlo()
            if config.tls == "required":
                if not server.has_extn("starttls"):
                    server.quit()
                    raise MailError("CONFIG", f"Server {config.host}:{config.port} tidak menawarkan STARTTLS, jadi tidak ada yang dikirim (SMTP_TLS=required).")
                server.starttls(context=self._context())
                server.ehlo()
        if config.user:
            server.login(config.user, config.password)
        return server

    def _classified(self, error: Exception) -> MailError:
        if isinstance(error, MailError):
            return error
        if isinstance(error, smtplib.SMTPAuthenticationError):
            return MailError("PERMISSION", f"SMTP menolak autentikasi: {error.smtp_code} {error.smtp_error!r}")
        if isinstance(error, smtplib.SMTPRecipientsRefused):
            return MailError("RECIPIENT_REJECTED", f"Alamat penerima ditolak: {list(error.recipients)}")
        if isinstance(error, smtplib.SMTPResponseException):
            kind = "TRANSIENT" if 400 <= error.smtp_code < 500 else "PAYLOAD"
            return MailError(kind, f"SMTP menjawab {error.smtp_code}: {error.smtp_error!r}")
        if isinstance(error, ssl.SSLError):
            return MailError("CONFIG", f"TLS ke server SMTP gagal: {error}. Periksa SMTP_CA_CERT_PATH dan nama host.")
        if isinstance(error, (smtplib.SMTPException, OSError, socket.timeout)):
            return MailError("TRANSIENT", f"SMTP tidak dapat dihubungi: {error}")
        return MailError("UNKNOWN", f"Pengiriman SMTP gagal: {error}")

    def verify(self) -> None:
        """Connects, negotiates TLS and logs in, without sending anything."""
        try:
            server = self._open()
            server.quit()
        except Exception as error:
            raise self._classified(error) from error

    def send(self, mail: Mail) -> str:
        message = EmailMessage()
        message["From"] = self.config.sender
        message["To"] = formataddr((mail.to_name, mail.to)) if mail.to_name else mail.to
        message["Subject"] = mail.subject
        message["Message-ID"] = make_msgid(domain=self.config.sender.split("@")[-1] or None)
        message.set_content("Buka email ini di aplikasi yang menampilkan HTML.")
        message.add_alternative(mail.html, subtype="html")
        try:
            server = self._open()
            try:
                server.send_message(message)
            finally:
                server.quit()
        except Exception as error:
            raise self._classified(error) from error
        return f"smtp:{message['Message-ID']}"


GRAPH_KEYS = ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET", "GRAPH_SENDER_MAILBOX")


@dataclass(frozen=True)
class GraphConfig:
    tenant_id: str
    client_id: str
    client_secret: str
    sender: str


def graph_problem(env: Mapping[str, str]) -> str | None:
    missing = [key for key in GRAPH_KEYS if not _trim(env, key)]
    return f"EMAIL_DRIVER=graph butuh {', '.join(missing)}." if missing else None


def graph_config(env: Mapping[str, str]) -> GraphConfig:
    problem = graph_problem(env)
    if problem:
        raise MailError("CONFIG", problem)
    return GraphConfig(_trim(env, "GRAPH_TENANT_ID"), _trim(env, "GRAPH_CLIENT_ID"),
                       env.get("GRAPH_CLIENT_SECRET", "").strip(), _trim(env, "GRAPH_SENDER_MAILBOX"))


class GraphMailer:
    """Microsoft Graph sendMail with the client-credentials flow."""

    name = "graph"
    LOGIN = "https://login.microsoftonline.com"
    GRAPH = "https://graph.microsoft.com/v1.0"

    def __init__(self, config: GraphConfig, client: httpx.Client | None = None):
        self.config = config
        self._client = client or httpx.Client(timeout=15)
        self._token: tuple[str, float] | None = None

    def _access_token(self) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        try:
            response = self._client.post(
                f"{self.LOGIN}/{self.config.tenant_id}/oauth2/v2.0/token",
                data={"client_id": self.config.client_id, "client_secret": self.config.client_secret,
                      "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"})
        except httpx.HTTPError as error:
            raise MailError("TRANSIENT", f"Tidak dapat menghubungi endpoint token Microsoft: {error}") from error
        if response.status_code >= 500:
            raise MailError("TRANSIENT", f"Endpoint token Microsoft sedang bermasalah (HTTP {response.status_code}).")
        if response.status_code != 200:
            raise MailError("PERMISSION", f"Gagal memperoleh token Graph (HTTP {response.status_code}). "
                                          "Periksa GRAPH_TENANT_ID, GRAPH_CLIENT_ID, dan GRAPH_CLIENT_SECRET.")
        payload = response.json()
        if not payload.get("access_token"):
            raise MailError("PERMISSION", "Respons token Microsoft tidak memuat access_token.")
        self._token = (payload["access_token"], time.time() + int(payload.get("expires_in") or 3600))
        return self._token[0]

    def verify(self) -> None:
        """Obtains a token, without sending anything."""
        self._access_token()

    def send(self, mail: Mail) -> str:
        token = self._access_token()
        recipient = {"address": mail.to, **({"name": mail.to_name} if mail.to_name else {})}
        body = {"message": {"subject": mail.subject, "body": {"contentType": "HTML", "content": mail.html},
                            "toRecipients": [{"emailAddress": recipient}]},
                "saveToSentItems": True}
        try:
            response = self._client.post(f"{self.GRAPH}/users/{quote(self.config.sender)}/sendMail",
                                         headers={"Authorization": f"Bearer {token}"}, json=body)
        except httpx.HTTPError as error:
            raise MailError("TRANSIENT", f"Tidak dapat menghubungi Microsoft Graph: {error}") from error
        if response.status_code == 429:
            raise MailError("TRANSIENT", "Graph membatasi laju pengiriman; dicoba lagi nanti.")
        if response.status_code in (401, 403):
            self._token = None
            raise MailError("PERMISSION", f"Graph menolak pengiriman (HTTP {response.status_code}). Pastikan izin "
                                          "aplikasi Mail.Send sudah diberi admin consent untuk mailbox pengirim.")
        if response.status_code >= 500:
            raise MailError("TRANSIENT", f"Graph sedang bermasalah (HTTP {response.status_code}).")
        if response.status_code != 202 and not response.is_success:
            raise MailError("PAYLOAD", f"Graph menolak pesan (HTTP {response.status_code}).")
        return f"graph:{response.headers.get('request-id') or uuid.uuid4().hex}"


class FileMailer:
    """A laptop without a mail server: every message written to a folder instead."""

    name = "file"

    def __init__(self, folder: Path):
        self.folder = folder

    def send(self, mail: Mail) -> str:
        self.folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%f")
        who = "".join(c if c.isalnum() or c in "._-" else "_" for c in mail.to)
        base = f"{stamp}__{who}__{uuid.uuid4().hex[:8]}"
        (self.folder / f"{base}.html").write_text(mail.html, encoding="utf-8")
        (self.folder / f"{base}.json").write_text(json.dumps({"to": mail.to, "subject": mail.subject}), encoding="utf-8")
        return f"file:{base}"


class RedirectingMailer:
    """Development only: every message goes to one inbox, with the real recipient in the subject."""

    def __init__(self, inner: Mailer, target: str):
        self.inner, self.target = inner, target
        self.name = f"{inner.name}+redirect"

    def send(self, mail: Mail) -> str:
        return self.inner.send(Mail(self.target, None, f"[untuk {mail.to}] {mail.subject}", mail.html))


def mailer_from_env(env: Mapping[str, str] | None = None, data_dir: Path | None = None) -> Mailer:
    env = os.environ if env is None else env
    production = _trim(env, "APP_ENV").lower() == "production"
    driver = _trim(env, "EMAIL_DRIVER").lower()
    if driver == "smtp":
        mailer: Mailer = SmtpMailer(smtp_config(env))
    elif driver == "file":
        if production:
            raise MailError("CONFIG", "EMAIL_DRIVER=file ditolak di production: email persetujuan tidak boleh berakhir di folder lokal.")
        folder = _trim(env, "EMAIL_FILE_DIR")
        mailer = FileMailer(Path(folder) if folder else (data_dir or Path("data")) / "outbox-mail")
    elif driver == "graph":
        mailer = GraphMailer(graph_config(env))
    elif driver == "gmail":
        raise MailError("CONFIG", "EMAIL_DRIVER=gmail tidak tersedia di versi ini. Untuk Gmail pakai smtp "
                                  "(smtp.gmail.com:587 dengan App Password).")
    else:
        raise MailError("CONFIG", "EMAIL_DRIVER belum diisi. Isi smtp atau graph untuk kantor, atau file untuk demo di laptop.")
    redirect = _trim(env, "EMAIL_REDIRECT_TO")
    if redirect:
        if production:
            raise MailError("CONFIG", "EMAIL_REDIRECT_TO ditolak di production: approver harus menerima permintaan persetujuannya sendiri.")
        mailer = RedirectingMailer(mailer, redirect)
    return mailer
