import base64

import pytest

from app.security import totp

# RFC 6238 appendix B, SHA-1; an authenticator shows the last six of the eight digits.
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode().rstrip("=")


@pytest.mark.parametrize(
    "seconds, code",
    [(59, "287082"), (1111111109, "081804"), (1111111111, "050471"),
     (1234567890, "005924"), (2000000000, "279037"), (20000000000, "353130")],
)
def test_matches_rfc_6238(seconds, code):
    assert totp.code_for(RFC_SECRET, totp.step_at(seconds)) == code


def test_accepts_one_step_either_side_and_nothing_further():
    now = 1_700_000_000.0
    step = totp.step_at(now)
    for offset in (-1, 0, 1):
        assert totp.match(RFC_SECRET, totp.code_for(RFC_SECRET, step + offset), now) == step + offset
    for offset in (-2, 2):
        assert totp.match(RFC_SECRET, totp.code_for(RFC_SECRET, step + offset), now) is None


def test_tolerates_spaces_but_nothing_that_is_not_six_digits():
    now = 1_700_000_000.0
    code = totp.code_for(RFC_SECRET, totp.step_at(now))
    assert totp.match(RFC_SECRET, f"{code[:3]} {code[3:]}", now) is not None
    for bad in ("12345", "abcdef", "", "1234567"):
        assert totp.match(RFC_SECRET, bad, now) is None


def test_generates_160_bit_secrets():
    secret = totp.generate_secret()
    assert len(secret) == 32 and secret.isalnum()
    assert secret != totp.generate_secret()


def test_otpauth_link_names_the_issuer_twice():
    uri = totp.otpauth_uri("HC Portal", "ayu.prameswari", "ABC234")
    assert uri.startswith("otpauth://totp/HC%20Portal%3Aayu.prameswari?")
    assert "issuer=HC+Portal" in uri and "secret=ABC234" in uri and "period=30" in uri
