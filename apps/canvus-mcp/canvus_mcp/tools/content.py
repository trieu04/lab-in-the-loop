"""Content tools: read notes and download PDF/image/asset bytes."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from canvus_sdk import NotFoundError

from canvus_mcp.client import get_client, get_settings
from canvus_mcp.content_download import CanvusContentDownloader

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def _dump(model: Any) -> dict[str, Any]:
    """Best-effort plain-dict view of an SDK model."""
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return dict(model) if isinstance(model, dict) else {"value": model}


def register(mcp: FastMCP, *, classification_for_canvas: Callable[[str], str]) -> None:
    """Attach content tools to ``mcp``.

    Every canvas-scoped read stamps the operator's ``data_classification`` for
    its canvas so the model boundary inherits authoritative locality instead of
    treating the read as unclassified (which fail-closes to ``unknown``). A
    missing widget returns a classified ``found: false`` rather than raising, so
    a stray 404 cannot poison a run with ``unknown``; other API errors still
    propagate and are handled conservatively downstream.
    """

    @mcp.tool()
    async def get_note(canvas_id: str, note_id: str) -> dict[str, Any]:
        """Get a Note widget's text and formatting.

        Returns the note's text, title, colors, and geometry.
        """
        client = get_client()
        data_classification = classification_for_canvas(canvas_id)
        try:
            note = await client.widgets.notes.get(canvas_id, note_id)
        except NotFoundError:
            return {
                "canvas_id": canvas_id,
                "note_id": note_id,
                "found": False,
                "data_classification": data_classification,
            }
        return {**_dump(note), "data_classification": data_classification}

    @mcp.tool()
    async def get_widget(canvas_id: str, widget_id: str) -> dict[str, Any]:
        """Get any widget by id, including its ``widget_type`` and ``mime_type``.

        Uses the generic widget endpoint; asset widgets expose ``hash``,
        ``mime_type``, and ``original_filename`` where the server provides them.
        """
        client = get_client()
        data_classification = classification_for_canvas(canvas_id)
        try:
            widget = await client.widgets.get(canvas_id, widget_id)
        except NotFoundError:
            return {
                "canvas_id": canvas_id,
                "widget_id": widget_id,
                "found": False,
                "data_classification": data_classification,
            }
        return {**_dump(widget), "data_classification": data_classification}

    @mcp.tool()
    async def download_pdf(canvas_id: str, pdf_id: str) -> dict[str, Any]:
        """Download a PDF widget's bytes to the output directory.

        Returns the saved file ``path``, ``mime_type``, ``size_bytes``, and
        ``sha256``. The bytes are not returned inline.
        """
        result = await CanvusContentDownloader(
            get_client(), output_dir=get_settings().mcp_output_dir
        ).acquire(canvas_id, "pdf", pdf_id)
        return {**result, "data_classification": classification_for_canvas(canvas_id)}

    @mcp.tool()
    async def download_image(canvas_id: str, image_id: str) -> dict[str, Any]:
        """Download an Image widget's bytes to the output directory.

        Returns the saved file ``path``, ``mime_type``, ``size_bytes``, and
        ``sha256``. The bytes are not returned inline.
        """
        result = await CanvusContentDownloader(
            get_client(), output_dir=get_settings().mcp_output_dir
        ).acquire(canvas_id, "image", image_id)
        return {**result, "data_classification": classification_for_canvas(canvas_id)}

    @mcp.tool()
    async def download_asset(asset_hash: str, canvas_id: str) -> dict[str, Any]:
        """Download a raw asset by its content hash to the output directory.

        Any image/video/pdf asset can be fetched by the ``hash`` reported on a
        widget. ``canvas_id`` names a canvas that contains the asset (required
        by the Canvus API as a ``canvas-id`` header).
        """
        result = await CanvusContentDownloader(
            get_client(), output_dir=get_settings().mcp_output_dir
        ).acquire(canvas_id, "asset", asset_hash)
        return {**result, "data_classification": classification_for_canvas(canvas_id)}


__all__ = ["register"]
