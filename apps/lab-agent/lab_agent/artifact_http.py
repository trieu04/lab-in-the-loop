"""HTTP-layer helpers for the artifact ASGI service (``artifact_server.py``):
uniform security headers, strong-ETag matching, and same-origin static
asset loading. Split out so header/ETag/static-loading behavior can be
exercised and read independently of route wiring.
"""

from __future__ import annotations

import importlib.resources
from functools import cache

from starlette.responses import Response

#: Applied to every response this service returns -- the rendered artifact
#: page, static assets, the health check, and the uniform 404 alike -- so no
#: response path accidentally omits them.
#:
#: ``frame-ancestors`` is intentionally left out of the CSP: whether Canvus
#: embeds this page via an <iframe> or opens it in a native browser view is
#: not established anywhere in this repo, and a restrictive value here could
#: silently blank the widget under whichever embedding mode we guessed wrong.
_CSP = (
    "default-src 'none'; style-src 'self'; script-src 'self'; "
    "img-src 'none'; font-src 'none'; connect-src 'none'; object-src 'none'; "
    "base-uri 'none'; form-action 'none'"
)

SECURITY_HEADERS: dict[str, str] = {
    "Cache-Control": "private, no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": _CSP,
}


def not_found() -> Response:
    """One uniform 404 for every unauthorized/missing/tampered/revoked/
    unknown-capability case -- identical status, body, and headers every
    time, so no response path leaks which of those it actually was."""
    return Response("Not found", status_code=404, media_type="text/plain", headers=dict(SECURITY_HEADERS))


def apply_security_headers(response: Response) -> Response:
    """Set every header in :data:`SECURITY_HEADERS` on ``response`` without
    clobbering a value a caller already set explicitly (e.g. a route-specific
    ``ETag``)."""
    for key, value in SECURITY_HEADERS.items():
        response.headers.setdefault(key, value)
    return response


def etag_for(content_hash: str) -> str:
    """Strong ETag (quoted, no ``W/`` weak-validator prefix) derived from an
    artifact version's content hash."""
    return f'"{content_hash}"'


def if_none_match_hits(if_none_match: str | None, etag: str) -> bool:
    """``True`` if ``etag`` is satisfied by a (possibly multi-valued or
    wildcard) ``If-None-Match`` request header, per RFC 9110 §13.1.2."""
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    candidates = {part.strip() for part in if_none_match.split(",")}
    return etag in candidates


#: Exact filenames this service will ever read from ``lab_agent/static/`` --
#: never derived from request input, so there is no path-traversal surface.
_STATIC_CONTENT_TYPES: dict[str, str] = {
    "artifact-view.css": "text/css; charset=utf-8",
    "artifact-tabs.js": "text/javascript; charset=utf-8",
}


@cache
def load_static_asset(filename: str) -> tuple[bytes, str]:
    """Read one package-local static asset by exact, allowlisted filename and
    return ``(bytes, content_type)``. Cached after first read -- these files
    do not change while the process is running.

    Raises :class:`KeyError` for any filename outside
    :data:`_STATIC_CONTENT_TYPES`; callers only ever pass a literal from that
    set, never request-derived input.
    """
    content_type = _STATIC_CONTENT_TYPES[filename]
    data = importlib.resources.files("lab_agent").joinpath("static", filename).read_bytes()
    return data, content_type


__all__ = [
    "SECURITY_HEADERS",
    "apply_security_headers",
    "etag_for",
    "if_none_match_hits",
    "load_static_asset",
    "not_found",
]
