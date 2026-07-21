"""Widget-creation tools: Note, Browser, Image, and Connector widgets."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import Context

from canvus_mcp.access_control import AccessDenied
from canvus_mcp.client import get_client

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from canvus_mcp.access_control import AccessPolicy


def _dump(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return dict(model) if isinstance(model, dict) else {"value": model}


def register(mcp: FastMCP, *, policy: AccessPolicy | None = None) -> None:
    """Attach widget-creation tools to ``mcp`` with optional central guarding."""

    def guard(action: str) -> Callable[[Any], Any]:
        if policy is not None:
            return policy.guarded(action)

        def deny(function: Any) -> Any:
            @wraps(function)
            async def fail_closed(*args: Any, **kwargs: Any) -> Any:
                raise AccessDenied()
            return fail_closed
        return deny

    @mcp.tool()
    @guard("create_note")
    async def create_note(
        canvas_id: str,
        text: str,
        x: float = 0.0,
        y: float = 0.0,
        width: float | None = None,
        height: float | None = None,
        title: str | None = None,
        background_color: str | None = None,
        text_color: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Create a Note widget at ``(x, y)`` on a canvas.

        Colors are ``#rrggbbaa`` hex strings. ``width``/``height`` are optional;
        the server picks defaults when omitted.
        """
        payload: dict[str, Any] = {"text": text, "location": {"x": x, "y": y}}
        if width is not None and height is not None:
            payload["size"] = {"width": width, "height": height}
        if title is not None:
            payload["title"] = title
        if background_color is not None:
            payload["background_color"] = background_color
        if text_color is not None:
            payload["text_color"] = text_color
        note = await get_client().widgets.notes.create(canvas_id, payload)
        return _dump(note)

    @mcp.tool()
    @guard("create_browser")
    async def create_browser(
        canvas_id: str,
        url: str,
        x: float = 0.0,
        y: float = 0.0,
        width: float | None = None,
        height: float | None = None,
        title: str | None = None,
        transparent_mode: bool | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Create a Browser widget at ``url`` and ``(x, y)``."""
        payload: dict[str, Any] = {"url": url, "location": {"x": x, "y": y}}
        if width is not None and height is not None:
            payload["size"] = {"width": width, "height": height}
        if title is not None:
            payload["title"] = title
            payload["name"] = title
        if transparent_mode is not None:
            payload["transparent_mode"] = transparent_mode
        browser = await get_client().widgets.browsers.create(canvas_id, payload)
        return _dump(browser)

    @mcp.tool()
    @guard("update_browser")
    async def update_browser(
        canvas_id: str,
        browser_id: str,
        url: str | None = None,
        title: str | None = None,
        transparent_mode: bool | None = None,
        x: float | None = None,
        y: float | None = None,
        width: float | None = None,
        height: float | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Update a Browser widget while preserving its id and canvas position."""
        payload: dict[str, Any] = {}
        if url is not None:
            payload["url"] = url
        if title is not None:
            payload["title"] = title
            payload["name"] = title
        if transparent_mode is not None:
            payload["transparent_mode"] = transparent_mode
        if x is not None and y is not None:
            payload["location"] = {"x": x, "y": y}
        if width is not None and height is not None:
            payload["size"] = {"width": width, "height": height}
        if not payload:
            raise ValueError(
                "update_browser requires at least one of: url, title, "
                "transparent_mode, x+y, width+height"
            )
        browser = await get_client().widgets.browsers.update(canvas_id, browser_id, payload)
        return _dump(browser)

    @mcp.tool()
    @guard("create_image")
    async def create_image(
        canvas_id: str,
        file_path: str,
        x: float = 0.0,
        y: float = 0.0,
        title: str | None = None,
        content_type: str = "application/octet-stream",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Create an Image widget by uploading a local file.

        Reads ``file_path`` from disk and uploads it as multipart form-data.
        ``title`` (if set) becomes the widget label — set it to ``RAGCluster_``
        to seed a RagCluster marker widget.
        """
        path = Path(file_path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"file_path does not exist: {path}")
        data = path.read_bytes()
        metadata: dict[str, Any] = {"location": {"x": x, "y": y}}
        if title is not None:
            metadata["title"] = title
        image = await get_client().widgets.images.upload(
            canvas_id,
            data,
            path.name,
            content_type=content_type,
            metadata=metadata,
        )
        return _dump(image)

    @mcp.tool()
    @guard("create_connector")
    async def create_connector(
        canvas_id: str,
        src_widget_id: str,
        dst_widget_id: str,
        line_color: str | None = None,
        line_width: float | None = None,
        connector_type: str | None = None,
        src_tip: str | None = None,
        dst_tip: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Create a directed Connector widget between two widgets."""
        payload: dict[str, Any] = {
            "src": {"id": src_widget_id, "auto_location": True},
            "dst": {"id": dst_widget_id, "auto_location": True},
        }
        if src_tip is not None:
            payload["src"]["tip"] = src_tip
        if dst_tip is not None:
            payload["dst"]["tip"] = dst_tip
        if line_color is not None:
            payload["line_color"] = line_color
        if line_width is not None:
            payload["line_width"] = line_width
        if connector_type is not None:
            payload["type"] = connector_type
        connector = await get_client().widgets.connectors.create(canvas_id, payload)
        return _dump(connector)


__all__ = ["register"]
