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

import structlog

from canvus_mcp.client import get_settings
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


def build_server():  # -> FastMCP
    """Construct the FastMCP instance with all tools registered."""
    from mcp.server.fastmcp import FastMCP

    cfg = get_settings()
    mcp = FastMCP(
        "canvus-mcp",
        instructions=INSTRUCTIONS,
        host=cfg.mcp_host,
        port=cfg.mcp_port,
    )
    register_all(mcp)
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
