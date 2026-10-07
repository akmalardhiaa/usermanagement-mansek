"""HTTP-level protection: where a request comes from, and the response headers.

The portal is plain HTML forms and no JavaScript at all, so the content
security policy can say exactly that: no script runs here, from anywhere.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'none'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)


def client_address(request: Request) -> str:
    """The last X-Forwarded-For entry (the hop nearest this server), else the socket."""
    forwarded = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
    if forwarded:
        return forwarded[-1]
    return request.client.host if request.client else "unknown"


def _origin(value: str | None) -> str | None:
    if not value:
        return None
    parts = urlsplit(value)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


def self_origins(request: Request, app_base_url: str) -> set[str]:
    """Every origin this portal is legitimately reached at for this request.

    The Host header is the site the browser is talking to — the one part of a
    forged cross-site request the attacker does not choose. X-Forwarded-Proto
    only changes the scheme for that same host (IIS terminating HTTPS).
    """
    origins = {o for o in (_origin(app_base_url),) if o}
    host = request.headers.get("host")
    if host:
        origins.add(f"{request.url.scheme}://{host}".lower())
        forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
        if forwarded in {"http", "https"}:
            origins.add(f"{forwarded}://{host}".lower())
    return origins


class ProtectionMiddleware(BaseHTTPMiddleware):
    """Refuses cross-site form posts, and sets the security headers on every response."""

    def __init__(self, app, app_base_url: str, production: bool, exempt_paths: tuple[str, ...] = ()):
        super().__init__(app)
        self._base = app_base_url
        self._production = production
        self._exempt = exempt_paths

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in UNSAFE_METHODS and not any(
            request.url.path == p or request.url.path.startswith(p + "/") for p in self._exempt
        ):
            expected = self_origins(request, self._base)
            given = _origin(request.headers.get("origin")) or _origin(request.headers.get("referer"))
            if given not in expected:
                response: Response = PlainTextResponse(
                    "Permintaan ditolak karena berasal dari asal yang tidak dikenal.", status_code=403
                )
                self._headers(response)
                return response

        response = await call_next(request)
        self._headers(response)
        return response

    def _headers(self, response: Response) -> None:
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        # same-origin, not no-referrer: under no-referrer a browser submits an
        # HTML form with `Origin: null`, and every form here would fail the
        # check above. same-origin still never hands a URL — an approval link
        # with its token, later — to another site.
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        if self._production:
            response.headers["Strict-Transport-Security"] = "max-age=15552000"
