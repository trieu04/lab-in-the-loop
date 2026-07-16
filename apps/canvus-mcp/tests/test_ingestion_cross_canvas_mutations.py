"""Cross-canvas ingestion access must not reveal or mutate shared-byte jobs."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from canvus_mcp.access_control import AccessDenied, AccessPolicy, Role
from canvus_mcp.ingestion_cache import AssetCache
from canvus_mcp.ingestion_pipeline import IngestionPipeline
from canvus_mcp.ingestion_store import IngestionStore
from canvus_mcp.ingestion_types import UnitSpec
from canvus_mcp.tools import ingestion


class _MCP:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *_args: Any, **_kwargs: Any):
        def register(function: Any) -> Any:
            self.tools[function.__name__] = function
            return function

        return register


def _operator_context() -> SimpleNamespace:
    return SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(headers=[("authorization", "Bearer operator-token")])
        )
    )


async def test_unrelated_canvas_cannot_read_retry_or_cancel_a_deduplicated_job(tmp_path: Path) -> None:
    store = IngestionStore(tmp_path / "ingestion.db")
    sha = "a" * 64
    store.upsert_asset(sha, size_bytes=1, mime_type="text/plain")
    store.record_source(canvas_id="canvas-a", source_ref="pdf:source-a", asset_sha256=sha)
    job, _ = store.create_job(sha, "text-v1", "text/plain", [UnitSpec("whole", 0)])
    mcp = _MCP()
    policy = AccessPolicy(
        store=store,
        reader_token=None,
        trusted_service_token=None,
        operator_token=SecretStr("operator-token"),
        reader_canvases=(),
        trusted_service_canvases=(),
        operator_canvases=("*",),
        stdio_role=Role.READER,
        stdio_canvases=(),
    )
    ingestion.register(
        mcp,
        policy=policy,
        pipeline=IngestionPipeline(store=store, cache=AssetCache(tmp_path / "cache")),
        max_chunk_chars=80,
    )
    try:
        for name in ("get_ingestion_status", "read_ingestion_chunks", "retry_ingestion", "cancel_ingestion"):
            with pytest.raises(AccessDenied, match="access_denied"):
                arguments = {"ctx": _operator_context()}
                if name == "read_ingestion_chunks":
                    arguments["max_chars"] = 80
                await mcp.tools[name]("canvas-b", job.id, **arguments)
        assert store.get_job(job.id).status.value == "queued"
        audit = store.conn.execute("SELECT reason FROM authorization_audit ORDER BY id").fetchall()
        assert [row[0] for row in audit] == ["resource_not_available"] * 4
    finally:
        store.close()
