"""Operator-authorized, content-free dependency health reporting."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from mcp.server.fastmcp import Context

from canvus_mcp.access_control import AccessPolicy
from canvus_mcp.client import get_client
from canvus_mcp.observability import InProcessCounters

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


health_counters = InProcessCounters(max_series=64)


class HealthRuntime(Protocol):
    store: Any
    pipeline: Any


async def health_snapshot(
    runtime: HealthRuntime, *, canvas_id: str | None = None, client: object | None = None
) -> dict[str, Any]:
    """Return fixed readiness categories without paths, URLs, or raw errors."""
    dependencies: dict[str, dict[str, str]] = {
        "process": {"status": "ok"},
        "ingestion_store": {"status": "blocked", "error_class": "storage_error"},
        "ingestion_cache": {"status": "blocked", "error_class": "storage_error"},
        "canvus": {"status": "blocked", "error_class": "dependency_error"},
        "ingestion_worker": {"status": "not_checked"},
    }
    try:
        problems = runtime.store.integrity_check()
        dependencies["ingestion_store"] = (
            {"status": "ok"}
            if not problems
            else {"status": "blocked", "error_class": "integrity_error"}
        )
    except Exception:  # Storage implementations do not share a safe exception base.
        pass

    cache = getattr(runtime.pipeline, "cache", None)
    root_fd = getattr(cache, "_root_fd", None)
    if isinstance(root_fd, int) and root_fd >= 0:
        dependencies["ingestion_cache"] = {"status": "ok"}

    try:
        canvus = client or get_client()
        canvases = getattr(canvus, "canvases")
        if canvas_id is None:
            await canvases.list()
        else:
            await canvases.get(canvas_id)
        dependencies["canvus"] = {"status": "ok"}
    except Exception as exc:  # SDK exceptions must never escape into health output.
        dependencies["canvus"] = {
            "status": "blocked",
            "error_class": _error_class(exc),
        }

    statuses = {item["status"] for item in dependencies.values()}
    overall = "blocked" if "blocked" in statuses else ("degraded" if statuses != {"ok"} else "ok")
    health_counters.increment("health_checks", tool="health", status=overall)
    return {"status": overall, "dependencies": dependencies}


def register(mcp: FastMCP, *, policy: AccessPolicy, runtime: HealthRuntime) -> None:
    """Register the detailed operator-only health tool."""

    @mcp.tool()
    @policy.guarded("health")
    async def health(
        canvas_id: str | None = None, ctx: Context | None = None
    ) -> dict[str, Any]:
        """Report sanitized readiness for one canvas, or globally with wildcard authority."""
        del ctx
        return await health_snapshot(runtime, canvas_id=canvas_id)


def _error_class(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, OSError):
        return "io_error"
    return "dependency_error"


__all__ = ["health_snapshot", "register"]
