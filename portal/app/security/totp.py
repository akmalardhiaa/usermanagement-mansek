"""Time-based one-time passwords (RFC 6238): the codes an authenticator app shows.

SHA-1, six digits, thirty seconds — what every authenticator assumes when the
otpauth link names nothing else, so Microsoft and Google Authenticator both
work. Written against the standard library: the algorithm is an HMAC over a
counter, and a second factor is the last place for a dependency nobody read.
"""

from __future__ import annotations

import base64
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

STEP_SECONDS = 30
DIGITS = 6
# One step either side of now: a code typed in the last seconds of its window,
# or a phone slightly out of step with the server, still works.
DRIFT_STEPS = 1


def generate_secret() -> str:
    """160 bits, base32 without padding — the length RFC 4226 recommends."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _decode(secret: str) -> bytes:
    clean = secret.replace(" ", "").replace("-", "").upper()
    return base64.b32decode(clean + "=" * (-len(clean) % 8))


def step_at(now: float) -> int:
    return int(now // STEP_SECONDS)


def code_for(secret: str, step: int) -> str:
    digest = hmac.new(_decode(secret), struct.pack(">Q", step), "sha1").digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % 10**DIGITS).zfill(DIGITS)


def match(secret: str, given: str, now: float | None = None) -> int | None:
    """The step a code belongs to, or None.

    The caller refuses any step at or below the last one it accepted, so a
    code read over somebody's shoulder cannot be used again in its window.
    """
    code = "".join(given.split())
    if len(code) != DIGITS or not code.isdigit():
        return None
    current = step_at(time.time() if now is None else now)
    for offset in range(-DRIFT_STEPS, DRIFT_STEPS + 1):
        step = current + offset
        if hmac.compare_digest(code_for(secret, step), code):
            return step
    return None


def otpauth_uri(issuer: str, account: str, secret: str) -> str:
    label = quote(f"{issuer}:{account}")
    query = urlencode(
        {"secret": secret, "issuer": issuer, "algorithm": "SHA1", "digits": DIGITS, "period": STEP_SECONDS}
    )
    return f"otpauth://totp/{label}?{query}"
