"""Everything the portal reads from the environment, read once and checked.

The names are the ones the portal has always used (APP_BASE_URL, LOGIN_2FA,
OUTBOX_ENCRYPTION_KEY, SESSION_IDLE_TTL_MINUTES, …), so an office .env written
for the earlier version keeps meaning the same thing.
"""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class ConfigurationError(Exception):
    """A configuration that cannot work; the portal refuses to start with it."""


def _flag_on(value: str | None) -> bool:
    return (value or "").strip().lower() in {"on", "true", "1", "yes"}


def _flag_off(value: str | None) -> bool:
    return (value or "").strip().lower() in {"off", "false", "0", "no"}


def _minutes(env: Mapping[str, str], name: str, default: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name}=\"{raw}\" bukan bilangan bulat menit.") from error
    if value <= 0:
        raise ConfigurationError(f"{name} harus lebih dari 0.")
    return value


@dataclass(frozen=True)
class Settings:
    production: bool
    app_base_url: str
    brand_name: str
    data_dir: Path
    session_file: Path
    mfa_file: Path
    security_log_file: Path
    ad_service_url: str
    ad_service_token: str
    encryption_key: bytes
    login_2fa: bool
    session_idle_minutes: int
    session_absolute_minutes: int

    @property
    def cookies_secure(self) -> bool:
        """Secure in production, unless the portal is explicitly published at
        http:// — the first hour on the office server, before IIS exists,
        when a secure cookie would simply never come back."""
        return self.production and not self.app_base_url.lower().startswith("http://")

    @property
    def mfa_issuer(self) -> str:
        return f"{self.brand_name} HC Portal" if self.brand_name else "HC Portal"

    @staticmethod
    def from_env(env: Mapping[str, str] | None = None) -> "Settings":
        env = dict(os.environ if env is None else env)
        production = (env.get("APP_ENV") or "").strip().lower() == "production"

        token = (env.get("AD_SERVICE_TOKEN") or "").strip()
        if len(token) < 24:
            raise ConfigurationError(
                "AD_SERVICE_TOKEN wajib diisi, minimal 24 karakter acak, sama dengan yang dipakai layanan AD."
            )

        raw_key = (env.get("OUTBOX_ENCRYPTION_KEY") or "").strip()
        if raw_key:
            try:
                key = base64.b64decode(raw_key, validate=True)
            except ValueError as error:
                raise ConfigurationError("OUTBOX_ENCRYPTION_KEY bukan base64 yang sah.") from error
            if len(key) != 32:
                raise ConfigurationError(f"OUTBOX_ENCRYPTION_KEY harus 32 byte dalam base64 (terbaca {len(key)} byte).")
        elif production:
            raise ConfigurationError("OUTBOX_ENCRYPTION_KEY wajib di production: kunci 2FA tidak boleh disimpan tanpa enkripsi.")
        else:
            key = hashlib.sha256(b"hc-demo-outbox-key").digest()

        base_url = (env.get("APP_BASE_URL") or "http://localhost:3000").strip().rstrip("/")
        data_dir = Path((env.get("HC_DATA_DIR") or "data").strip())

        def path(name: str, default: str) -> Path:
            configured = (env.get(name) or "").strip()
            return Path(configured) if configured else data_dir / default

        return Settings(
            production=production,
            app_base_url=base_url,
            brand_name=(env.get("NEXT_PUBLIC_BRAND_NAME") or env.get("BRAND_NAME") or "").strip(),
            data_dir=data_dir,
            session_file=path("HC_SESSION_FILE", "hc-sessions.json"),
            mfa_file=path("HC_MFA_FILE", "hc-mfa.json"),
            security_log_file=path("HC_SECURITY_LOG_FILE", "hc-security-log.jsonl"),
            ad_service_url=(env.get("AD_SERVICE_URL") or "http://ad-service:8081").strip().rstrip("/"),
            ad_service_token=token,
            encryption_key=key,
            login_2fa=not _flag_off(env.get("LOGIN_2FA")),
            session_idle_minutes=_minutes(env, "SESSION_IDLE_TTL_MINUTES", 30),
            session_absolute_minutes=_minutes(env, "SESSION_ABSOLUTE_TTL_MINUTES", 8 * 60),
        )
