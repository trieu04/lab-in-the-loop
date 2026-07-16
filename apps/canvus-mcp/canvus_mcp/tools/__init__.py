"""FastMCP tool registration package.

Each module exposes ``register(mcp)`` which attaches its tools to the shared
:class:`~mcp.server.fastmcp.FastMCP` instance. ``register_all`` wires them all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from canvus_mcp.tools import connections, content, experiments, scan, widgets

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register_all(mcp: FastMCP) -> None:
    """Register every tool module on ``mcp``."""
    scan.register(mcp)
    content.register(mcp)
    widgets.register(mcp)
    connections.register(mcp)
    experiments.register(mcp)


__all__ = ["register_all"]
