"""The portal's only way to Active Directory: the Java AD service.

Every call carries the shared AD_SERVICE_TOKEN. The service is reachable only
inside the Podman network.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class LoginResult:
    """Exactly one of `user` or `code` is set.

    code: INVALID_CREDENTIALS, AD_UNAVAILABLE, NOT_CONFIGURED, or
    SERVICE_UNAVAILABLE when the AD service itself could not be reached.
    """

    user: dict | None = None
    code: str | None = None
    message: str | None = None


class AdClient:
    def __init__(self, base_url: str, token: str, timeout: float = 15.0):
        self._base = base_url
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = timeout

    def login(self, username: str, password: str) -> LoginResult:
        try:
            response = httpx.post(
                f"{self._base}/v1/auth/login",
                json={"username": username, "password": password},
                headers=self._headers,
                timeout=self._timeout,
            )
        except httpx.HTTPError as error:
            return LoginResult(code="SERVICE_UNAVAILABLE", message=f"Layanan AD tidak bisa dihubungi: {error}")

        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code == 200 and "user" in body:
            return LoginResult(user=body["user"])
        return LoginResult(code=body.get("code", "SERVICE_UNAVAILABLE"), message=body.get("error"))

    def health(self) -> dict | None:
        try:
            response = httpx.get(f"{self._base}/v1/health", timeout=5.0)
            return response.json() if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None
