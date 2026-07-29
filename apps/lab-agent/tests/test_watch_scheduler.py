from __future__ import annotations

import asyncio

import pytest

from lab_agent import watch
from lab_agent.config import Settings
from lab_agent.observability import EventContext
from lab_agent.tenant import CanvasAccessDeniedError, TenantContext


@pytest.mark.asyncio
async def test_process_canvases_once_bounds_concurrency(monkeypatch) -> None:
    active = 0
    peak = 0
    context = EventContext("runtime", "tenant-a", "canvas-a", stage="watch")
    before = watch.watch_metrics.counter_value("canvas_cycles", context, status="ok")

    async def fake_process_once(*args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {"setups": 1, "runs": 0, "loops": 0, "validations": 0}

    monkeypatch.setattr(watch, "process_once", fake_process_once)
    results = await watch.process_canvases_once(
        object(),
        lambda _canvas_id: object(),
        Settings(),
        object(),
        "runtime",
        ["canvas-a", "canvas-b", "canvas-c", "canvas-d"],
        max_concurrent=2,
        tenant_id="tenant-a",
    )

    assert peak == 2
    assert watch.watch_metrics.counter_value("canvas_cycles", context, status="ok") == before + 1
    assert set(results) == {"canvas-a", "canvas-b", "canvas-c", "canvas-d"}
    assert all(item["setups"] == 1 for item in results.values())


@pytest.mark.asyncio
async def test_process_canvases_once_isolates_one_canvas_failure(monkeypatch) -> None:
    visited: list[str] = []

    async def fake_process_once(*args, **kwargs):
        canvas_id = args[5]
        visited.append(canvas_id)
        if canvas_id == "canvas-b":
            raise RuntimeError("sensitive provider detail")
        return {"setups": 0, "runs": 1, "loops": 0, "validations": 0}

    monkeypatch.setattr(watch, "process_once", fake_process_once)
    results = await watch.process_canvases_once(
        object(),
        lambda _canvas_id: object(),
        Settings(),
        object(),
        "runtime",
        ["canvas-a", "canvas-b", "canvas-c"],
        max_concurrent=2,
    )

    assert visited == ["canvas-a", "canvas-b", "canvas-c"]
    assert results["canvas-a"]["runs"] == 1
    assert results["canvas-b"] == {"setups": 0, "runs": 0, "loops": 0, "validations": 0}
    assert results["canvas-c"]["runs"] == 1


@pytest.mark.asyncio
async def test_process_canvases_once_rejects_zero_concurrency() -> None:
    with pytest.raises(ValueError, match="max_concurrent"):
        await watch.process_canvases_once(
            object(),
            lambda _canvas_id: object(),
            Settings(),
            object(),
            "runtime",
            ["canvas-a"],
            max_concurrent=0,
        )


@pytest.mark.asyncio
async def test_process_canvases_once_rejects_duplicate_canvases(monkeypatch) -> None:
    calls = 0

    async def fake_process_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"setups": 0, "runs": 0, "loops": 0, "validations": 0}

    monkeypatch.setattr(watch, "process_once", fake_process_once)
    with pytest.raises(ValueError, match="duplicates"):
        await watch.process_canvases_once(
            object(),
            lambda _canvas_id: object(),
            Settings(),
            object(),
            "runtime",
            ["canvas-a", "canvas-a"],
            max_concurrent=2,
        )
    assert calls == 0


@pytest.mark.asyncio
async def test_process_canvases_once_rejects_canvas_outside_tenant_scope() -> None:
    context = TenantContext("tenant-a", ["canvas-a"], "research.example")

    with pytest.raises(CanvasAccessDeniedError):
        await watch.process_canvases_once(
            object(),
            lambda _canvas_id: object(),
            Settings(),
            object(),
            "runtime",
            ["canvas-a", "canvas-b"],
            tenant_context=context,
        )
