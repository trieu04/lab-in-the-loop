"""Capability-protected ASGI artifact service (Phase 3).

Serves the rendered artifact HTML at stable capability URLs
(``/artifacts/{opaque_id}?token=...``), same-origin static assets, and a
non-sensitive health check.

Every unauthorized/missing/tampered/revoked/unknown-capability case returns
the exact same uniform 404 (:func:`lab_agent.artifact_http.not_found`) --
there is no response path that lets an adversary distinguish "wrong token"
from "no such artifact", matching ``ArtifactStore.get_authorized_artifact``'s
own fail-quiet contract.

Route handlers are plain sync functions; Starlette runs them in its worker
thread pool. ``ArtifactStore`` shares one SQLite connection
(``check_same_thread=False``, see ``state/connection.py``) across every
request thread, so every handler that touches it does so inside a single
``threading.Lock`` -- simple, correct serialization instead of relying on
SQLite's own thread-safety mode under concurrent Python threads.
"""

from __future__ import annotations

import hashlib
import threading

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from lab_agent.artifact_http import (
    apply_security_headers,
    etag_for,
    if_none_match_hits,
    load_static_asset,
    not_found,
)
from lab_agent.artifact_render import render_artifact_html
from lab_agent.artifact_store import ArtifactStore


def _static_response(filename: str) -> Response:
    data, content_type = load_static_asset(filename)
    return apply_security_headers(Response(data, media_type=content_type))


def create_artifact_app(store: ArtifactStore) -> Starlette:
    """Build the Starlette ASGI app serving one process's ``ArtifactStore``.

    One ``threading.Lock`` per returned app instance serializes every
    request's use of ``store``'s shared SQLite connection.
    """
    store_lock = threading.Lock()

    def artifact_view(request: Request) -> Response:
        opaque_id = request.path_params["opaque_id"]
        token = request.query_params.get("token")
        if not token:
            return not_found()

        with store_lock:
            document = store.get_authorized_artifact(opaque_id, token=token)
            if document is None:
                return not_found()
            versions = store.list_versions(opaque_id, canvas_id=document.canvas_id)

        rendered = render_artifact_html(document, versions)
        body_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        etag = etag_for(body_hash)
        if if_none_match_hits(request.headers.get("if-none-match"), etag):
            response = Response(status_code=304)
        else:
            response = Response(rendered, media_type="text/html")
        response.headers["ETag"] = etag
        return apply_security_headers(response)

    def artifact_css(request: Request) -> Response:
        return _static_response("artifact-view.css")

    def artifact_js(request: Request) -> Response:
        return _static_response("artifact-tabs.js")

    def healthz(request: Request) -> Response:
        # Deliberately static: no DB path, config, or capability token ever
        # belongs in a health-check response.
        return apply_security_headers(Response('{"status":"ok"}', media_type="application/json"))

    return Starlette(
        routes=[
            Route("/artifacts/{opaque_id}", artifact_view, methods=["GET"]),
            Route("/assets/artifact-view.css", artifact_css, methods=["GET"]),
            Route("/assets/artifact-tabs.js", artifact_js, methods=["GET"]),
            Route("/healthz", healthz, methods=["GET"]),
        ]
    )


__all__ = ["create_artifact_app"]
