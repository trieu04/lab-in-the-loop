"""Concurrency-serialization test for the artifact ASGI service.

Proves ``create_artifact_app``'s per-app ``threading.Lock`` actually
serializes two request threads' use of the shared ``ArtifactStore``/SQLite
connection -- not just that it compiles.

Drives the ``artifact_view`` route handler directly (via
``app.routes[0].endpoint``) with a lightweight duck-typed request, rather
than through Starlette's TestClient/ASGI portal, so the assertion is about
this module's own lock -- not about how the ASGI transport happens to
schedule worker threads. This is deterministic ordering, not a timing
assertion: a short ``time.sleep`` while "inside the store" widens the race
window enough that an unlocked implementation would reliably interleave.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from lab_agent.artifact_server import create_artifact_app
from lab_agent.artifact_store import ArtifactStore
from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
from lab_agent.models.states import DecisionState
from lab_agent.state_store import StateStore

PROVENANCE = ArtifactProvenance(provider="claude", model_name="claude-x", trigger_id="t1")


class _FakeRequest:
    """Duck-typed stand-in for ``starlette.requests.Request`` -- the handler
    only ever reads ``path_params``, ``query_params.get``, and
    ``headers.get``, all of which plain dicts already satisfy."""

    def __init__(self, opaque_id: str, token: str) -> None:
        self.path_params = {"opaque_id": opaque_id}
        self.query_params = {"token": token}
        self.headers: dict[str, str] = {}


@pytest.fixture
def artifact_store(store: StateStore, clock) -> ArtifactStore:
    return ArtifactStore(store.conn, clock=clock)


def test_concurrent_requests_never_overlap_inside_the_store_lock(artifact_store: ArtifactStore) -> None:
    doc = artifact_store.create_artifact(
        canvas_id="c1",
        artifact_type=ArtifactType.SETUP,
        state=DecisionState.DRAFT,
        payload={"title": "Setup A"},
        provenance=PROVENANCE,
    )
    token = artifact_store.issue_token(doc.opaque_id, canvas_id="c1")
    events: list[str] = []
    real_get = artifact_store.get_authorized_artifact

    def slow_get(opaque_id: str, *, token: str):
        events.append("enter")
        time.sleep(0.05)
        events.append("exit")
        return real_get(opaque_id, token=token)

    artifact_store.get_authorized_artifact = slow_get  # type: ignore[method-assign]

    app = create_artifact_app(artifact_store)
    # `.endpoint` is a `Route`-only attribute untyped on the generic
    # `BaseRoute` list Starlette exposes -- justified narrow escape so this
    # test can invoke the sync handler on its own threads directly.
    artifact_view: Callable[[Any], Any] = app.routes[0].endpoint  # type: ignore[attr-defined]

    threads = [
        threading.Thread(target=artifact_view, args=(_FakeRequest(doc.opaque_id, token),))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert events == ["enter", "exit", "enter", "exit"]
