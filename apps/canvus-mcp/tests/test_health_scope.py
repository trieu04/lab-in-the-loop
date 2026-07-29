"""Health probes preserve the operator's canvas authorization boundary."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from canvus_mcp.access_control import AccessDenied, AccessPolicy, Principal, Role
from canvus_mcp.ingestion_store import IngestionStore
from canvus_mcp.tools.health import health_snapshot


class Store:
    def integrity_check(self) -> list[str]:
        return []


class Canvases:
    def __init__(self) -> None:
        self.list_calls = 0
        self.get_calls: list[str] = []

    async def list(self) -> list[object]:
        self.list_calls += 1
        return []

    async def get(self, canvas_id: str) -> object:
        self.get_calls.append(canvas_id)
        return object()


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(
        store=Store(), pipeline=SimpleNamespace(cache=SimpleNamespace(_root_fd=1))
    )


def _policy(store: IngestionStore, canvases: tuple[str, ...]) -> AccessPolicy:
    return AccessPolicy(
        store=store, reader_token=None, trusted_service_token=None,
        operator_token=SecretStr("operator"), reader_canvases=(),
        trusted_service_canvases=(), operator_canvases=canvases,
        stdio_role=Role.READER, stdio_canvases=(),
    )


def test_canvas_operator_cannot_authorize_global_health_listing(tmp_path) -> None:
    store = IngestionStore(tmp_path / "ingestion.db")
    policy = _policy(store, ("canvas-a",))
    try:
        with pytest.raises(AccessDenied):
            policy.require(Principal(Role.OPERATOR, "bearer"), action="health", canvas_id=None)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_scoped_health_probes_only_authorized_canvas() -> None:
    canvases = Canvases()

    report = await health_snapshot(
        _runtime(), canvas_id="canvas-a", client=SimpleNamespace(canvases=canvases)
    )

    assert report["dependencies"]["canvus"] == {"status": "ok"}
    assert canvases.get_calls == ["canvas-a"]
    assert canvases.list_calls == 0


@pytest.mark.asyncio
async def test_wildcard_operator_can_use_backward_compatible_global_health(tmp_path) -> None:
    store = IngestionStore(tmp_path / "ingestion.db")
    policy = _policy(store, ("*",))
    canvases = Canvases()
    try:
        policy.require(Principal(Role.OPERATOR, "bearer"), action="health", canvas_id=None)
        report = await health_snapshot(_runtime(), client=SimpleNamespace(canvases=canvases))
    finally:
        store.close()

    assert report["dependencies"]["canvus"] == {"status": "ok"}
    assert canvases.list_calls == 1
    assert canvases.get_calls == []
