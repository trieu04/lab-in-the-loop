"""Canvus MCP server — FastMCP instance and streamable-HTTP entrypoint.

Exposes Canvus tools over the Model Context Protocol:

- ``scan_server`` / ``list_canvases`` — inspect the pre-configured server.
- ``get_note`` / ``get_widget`` / ``download_pdf`` / ``download_image`` /
  ``download_asset`` — read and download content.
- ``create_note`` / ``create_browser`` / ``create_image`` /
  ``create_connector`` — create widgets and connections.
- ``check_widget_connections`` / ``check_ragcluster_connections`` — inspect
  what is connected to a widget (e.g. a RagCluster Image).

Run with ``canvus-mcp`` (console script) or ``python -m canvus_mcp.server``.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import structlog

from canvus_mcp.access_control import AccessPolicy, Role
from canvus_mcp.client import close_client, get_settings
from canvus_mcp.config import Settings
from canvus_mcp.extractors import ExtractorLimits, LocalExtractor
from canvus_mcp.ingestion_cache import AssetCache
from canvus_mcp.ingestion_pipeline import IngestionPipeline
from canvus_mcp.ingestion_store import IngestionStore
from canvus_mcp.tools import register_all

log = structlog.get_logger(__name__)


def configure_stderr_logging() -> None:
    """Route all logs to stderr.

    Mandatory for the stdio transport: stdout is the JSON-RPC channel, so any
    log written there (including canvus-sdk's per-request structlog lines)
    corrupts the protocol stream. Sending everything to stderr keeps stdout
    clean and is harmless for the HTTP transport too.
    """
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, force=True)
    structlog.configure(
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


# Injected into the client model's context via the MCP ``initialize``
# handshake (the protocol-level ``instructions`` field), so every consumer
# gets this guidance with zero client-side configuration. Guards against a
# failure mode observed in the wild: after one transient "No such tool
# available" error, the client model invents a "prefix-stripping" theory and
# retries with mangled names (e.g. ``_canvus__scan_server``), failing forever.
INSTRUCTIONS = """\
Canvus MCP server: inspect, download from, and create widgets on a \
pre-configured Canvus collaboration server.

Tool naming: call every tool by its EXACT registered name (e.g. \
`scan_server`, `check_ragcluster_connections`). If your client namespaces \
tools (e.g. `mcp__canvus__scan_server`), use that full namespaced name \
verbatim. Never strip, shorten, or "compensate" any prefix — a name like \
`_canvus__scan_server` is always wrong. If a call fails with "No such tool \
available", re-check the exact name from the tool list; do not rename.

Typical flow: `list_canvases` or `scan_server` to find canvases and \
RagCluster widgets, then `check_ragcluster_connections` / \
`check_widget_connections` with the canvas id. Canvas ids come from \
`list_canvases`; widget ids from scan/connection results.\
"""


@dataclass
class IngestionRuntime:
    """One server-owned durable store, cache, pipeline, and access policy."""

    store: IngestionStore
    pipeline: IngestionPipeline
    policy: AccessPolicy
    _closed: bool = False

    def close(self) -> None:
        """Close descriptor-owning cache and database once, in dependency order."""
        if self._closed:
            return
        self._closed = True
        try:
            self.pipeline.cache.close()
        finally:
            self.store.close()


def build_ingestion_runtime(cfg: Settings) -> IngestionRuntime:
    """Create server dependencies once; unwind every partial construction."""
    store = IngestionStore(Path(cfg.mcp_ingestion_db_path))
    cache: AssetCache | None = None
    try:
        limits = ExtractorLimits(
            max_source_bytes=cfg.mcp_ingestion_max_source_bytes,
            max_output_chars=cfg.mcp_ingestion_max_output_chars,
            max_chunk_chars=cfg.mcp_ingestion_chunk_char_cap,
            max_records=cfg.mcp_ingestion_max_records,
            max_pdf_pages=cfg.mcp_ingestion_max_pdf_pages,
            pdf_password_file=Path(cfg.mcp_ingestion_pdf_password_file) if cfg.mcp_ingestion_pdf_password_file else None,
        )
        cache = AssetCache(Path(cfg.mcp_ingestion_cache_dir))
        pipeline = IngestionPipeline(store=store, cache=cache, extractor=LocalExtractor(limits))
        policy = AccessPolicy(
            store=store, reader_token=cfg.mcp_reader_token,
            trusted_service_token=cfg.mcp_trusted_service_token, operator_token=cfg.mcp_operator_token,
            reader_canvases=tuple(cfg.mcp_reader_canvases),
            trusted_service_canvases=tuple(cfg.mcp_trusted_service_canvases),
            operator_canvases=tuple(cfg.mcp_operator_canvases), stdio_role=Role(cfg.mcp_stdio_role),
            stdio_canvases=tuple(cfg.mcp_stdio_canvases),
        )
        return IngestionRuntime(store=store, pipeline=pipeline, policy=policy)
    except BaseException:
        if cache is not None:
            try:
                cache.close()
            finally:
                store.close()
        else:
            store.close()
        raise


def _lifespan(runtime: IngestionRuntime):
    """Close server-owned SQLite and HTTP resources for every transport."""
    @asynccontextmanager
    async def manage(_mcp):
        try:
            yield runtime
        finally:
            try:
                runtime.close()
            finally:
                await close_client()
    return manage


def _classification(cfg: Settings, canvas_id: str) -> str:
    """Only operator configuration supplies a source data classification."""
    return cfg.canvas_classifications.get(canvas_id, "unknown")


def build_server():  # -> FastMCP
    """Construct the FastMCP instance with a single shared ingestion lifecycle."""
    from mcp.server.fastmcp import FastMCP

    cfg = get_settings()
    runtime = build_ingestion_runtime(cfg)
    try:
        mcp = FastMCP(
            "canvus-mcp", instructions=INSTRUCTIONS, host=cfg.mcp_host, port=cfg.mcp_port,
            lifespan=_lifespan(runtime),
        )
        register_all(
            mcp, runtime=runtime,
            max_chunk_chars=cfg.mcp_ingestion_chunk_char_cap,
            max_source_bytes=cfg.mcp_ingestion_max_source_bytes,
            classification_for_canvas=lambda canvas_id: _classification(cfg, canvas_id),
        )
    except BaseException:
        runtime.close()
        raise
    mcp.ingestion_runtime = runtime
    return mcp


def main() -> None:
    """Console entrypoint.

    Serves over streamable HTTP by default; pass ``--stdio`` to run over stdio
    instead (useful when an MCP client such as Claude Code launches and manages
    the process itself).
    """
    configure_stderr_logging()
    mcp = build_server()
    cfg = get_settings()
    if "--stdio" in sys.argv:
        log.info("canvus_mcp_start", transport="stdio")
        mcp.run(transport="stdio")
        return
    log.info("canvus_mcp_start", transport="streamable-http", host=cfg.mcp_host, port=cfg.mcp_port)
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
