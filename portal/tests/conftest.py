from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ad_client import LoginResult  # noqa: E402
from app.main import create_app  # noqa: E402
from app.settings import Settings  # noqa: E402

ORIGIN = {"Origin": "http://testserver"}

USERS = {
    "admin": ("admin12345", {"id": "admin", "username": "admin", "email": "admin@example.com",
                             "fullName": "akmalardhia", "department": "Human Capital",
                             "roles": ["SYSTEM_ADMIN", "HC_REQUESTER"]}),
    "bagus": ("bagus12345", {"id": "bagus", "username": "bagus", "email": "bagus@example.com",
                             "fullName": "Bagus Nugroho", "department": "IT", "roles": []}),
}


class FakeAd:
    """Stands in for the Java AD service: same answers, no network."""

    def __init__(self) -> None:
        self.calls = 0
        self.down = False

    def login(self, username: str, password: str) -> LoginResult:
        self.calls += 1
        if self.down:
            return LoginResult(code="AD_UNAVAILABLE", message="down")
        known = USERS.get(username.lower())
        if known and known[0] == password:
            return LoginResult(user=dict(known[1]))
        return LoginResult(code="INVALID_CREDENTIALS", message="Username atau kata sandi salah.")

    def health(self) -> dict:
        return {"ok": True, "directory": "demo"}


def make_settings(tmp_path: Path, **overrides: str) -> Settings:
    env = {
        "AD_SERVICE_TOKEN": "a-service-token-of-24-chars",
        "HC_DATA_DIR": str(tmp_path),
        "APP_BASE_URL": "http://testserver",
        **overrides,
    }
    return Settings.from_env(env)


@pytest.fixture
def ad() -> FakeAd:
    return FakeAd()


@pytest.fixture
def make_client(tmp_path, ad):
    def build(**overrides: str) -> TestClient:
        app = create_app(make_settings(tmp_path, **overrides), ad)
        return TestClient(app, base_url="http://testserver", follow_redirects=False)

    return build


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()
