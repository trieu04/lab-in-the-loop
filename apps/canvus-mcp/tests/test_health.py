from __future__ import annotations

from types import SimpleNamespace

import pytest

from canvus_mcp.observability import InProcessCounters
from canvus_mcp.tools.health import health_counters, health_snapshot


class Store:
    def __init__(self, problems: list[str] | None = None) -> None:
        self.problems = problems or []

    def integrity_check(self) -> list[str]:
        return self.problems


class Canvases:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def list(self) -> list[object]:
        if self.error is not None:
            raise self.error
        return []


def runtime(*, problems: list[str] | None = None, root_fd: int = 3):
    cache = SimpleNamespace(_root_fd=root_fd)
    pipeline = SimpleNamespace(cache=cache)
    return SimpleNamespace(store=Store(problems), pipeline=pipeline)


@pytest.mark.asyncio
async def test_health_snapshot_reports_fixed_dependency_states() -> None:
    before = sum(series.value for series in health_counters.snapshot())
    report = await health_snapshot(
        runtime(),
        client=SimpleNamespace(canvases=Canvases()),
    )

    assert sum(series.value for series in health_counters.snapshot()) == before + 1
    assert report["status"] == "degraded"
    assert report["dependencies"] == {
        "process": {"status": "ok"},
        "ingestion_store": {"status": "ok"},
        "ingestion_cache": {"status": "ok"},
        "canvus": {"status": "ok"},
        "ingestion_worker": {"status": "not_checked"},
    }


@pytest.mark.asyncio
async def test_health_snapshot_redacts_dependency_failures() -> None:
    secret = "https://secret.example/?token=top-secret"
    report = await health_snapshot(
        runtime(problems=[secret], root_fd=-1),
        client=SimpleNamespace(canvases=Canvases(RuntimeError(secret))),
    )

    assert report["status"] == "blocked"
    assert report["dependencies"]["ingestion_store"] == {
        "status": "blocked",
        "error_class": "integrity_error",
    }
    assert report["dependencies"]["canvus"] == {
        "status": "blocked",
        "error_class": "dependency_error",
    }
    assert secret not in repr(report)


def test_counters_bound_and_redact_series() -> None:
    counters = InProcessCounters(max_series=1)
    secret = "Bearer top-secret"

    assert counters.increment("tool_calls", tool="health", status="ok", content=secret)
    assert not counters.increment("tool_errors", tool="health", status="blocked")
    assert counters.dropped_series == 1
    assert counters.snapshot()[0].labels == (("status", "ok"), ("tool", "health"))
    assert secret not in repr(counters.snapshot())
