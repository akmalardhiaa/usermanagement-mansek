"""The portal's only way to Active Directory: the Java AD service.

Every call carries the shared AD_SERVICE_TOKEN. The service is reachable only
inside the Podman network.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

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


class AdError(Exception):
    """A directory failure, with the kind the Java service classified it as.

    TRANSIENT (try later), PERMISSION, CONFLICT, NOT_FOUND, TIMEOUT_AFTER_WRITE
    (a write may have happened), NOT_CONFIGURED, UNKNOWN.
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind

    @property
    def retryable(self) -> bool:
        return self.kind == "TRANSIENT"


class AdClient:
    def __init__(self, base_url: str, token: str, timeout: float = 30.0):
        self._base = base_url
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = timeout

    # ---------------------------------------------------------------- sign-in

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

    # -------------------------------------------------------------- directory

    def _call(self, method: str, path: str, *, json: dict | None = None, params: dict | None = None,
              allow_404: bool = False):
        try:
            response = httpx.request(method, f"{self._base}{path}", json=json, params=params,
                                     headers=self._headers, timeout=self._timeout)
        except httpx.TimeoutException as error:
            # A write that timed out may have happened; a read can simply be retried.
            kind = "TIMEOUT_AFTER_WRITE" if method != "GET" else "TRANSIENT"
            raise AdError(kind, f"Layanan AD tidak menjawab: {error}") from error
        except httpx.HTTPError as error:
            raise AdError("TRANSIENT", f"Layanan AD tidak bisa dihubungi: {error}") from error

        if response.status_code == 404 and allow_404:
            return None
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {}
            raise AdError(body.get("kind", "UNKNOWN"), body.get("error") or f"Layanan AD menjawab {response.status_code}.")
        return response.json() if response.content else {}

    def list_accounts(self, base: str) -> list[dict]:
        return self._call("GET", "/v1/accounts", params={"base": base})["accounts"]

    def find_by_name(self, name: str) -> dict | None:
        return self._call("GET", f"/v1/accounts/by-name/{quote(name, safe='')}", allow_404=True)

    def find_by_guid(self, guid: str) -> dict | None:
        return self._call("GET", f"/v1/accounts/by-guid/{quote(guid, safe='')}", allow_404=True)

    def standing(self, name: str) -> dict:
        return self._call("GET", f"/v1/accounts/by-name/{quote(name, safe='')}/standing")

    def group_members(self, group_dn: str) -> list[dict]:
        return self._call("GET", "/v1/groups/members", params={"dn": group_dn})["members"]

    def create_account(self, spec: dict) -> dict:
        return self._call("POST", "/v1/accounts", json=spec)

    def set_attributes(self, guid: str, patch: dict) -> None:
        self._call("PATCH", f"/v1/accounts/{quote(guid, safe='')}", json=patch)

    def change_groups(self, guid: str, add: list[str], remove: list[str]) -> None:
        self._call("POST", f"/v1/accounts/{quote(guid, safe='')}/groups", json={"add": add, "remove": remove})

    def move(self, guid: str, ou: str) -> None:
        self._call("POST", f"/v1/accounts/{quote(guid, safe='')}/ou", json={"ou": ou})

    def set_enabled(self, guid: str, enabled: bool) -> None:
        self._call("POST", f"/v1/accounts/{quote(guid, safe='')}/enabled", json={"enabled": enabled})

    def entry_exists(self, dn: str) -> bool:
        return bool(self._call("POST", "/v1/entries/exists", json={"dn": dn})["exists"])
