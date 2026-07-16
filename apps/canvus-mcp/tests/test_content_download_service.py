"""Shared Canvus content acquisition keeps download response contracts stable."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from canvus_mcp.content_download import CanvusContentDownloader, SourceRequestError


@dataclass
class _Widget:
    original_filename: str = "report.pdf"
    mime_type: str = "application/pdf"


@dataclass
class _Pdfs:
    async def get(self, canvas_id: str, source_id: str) -> _Widget:
        return _Widget()

    async def download(self, canvas_id: str, source_id: str) -> bytes:
        return b"%PDF-1.7 test"


@dataclass
class _Assets:
    async def download_by_hash(self, source_id: str, canvas_id: str) -> bytes:
        return b"asset-bytes"


@dataclass
class _Widgets:
    pdfs: _Pdfs = field(default_factory=_Pdfs)


@dataclass
class _Client:
    widgets: _Widgets = field(default_factory=_Widgets)
    assets: _Assets = field(default_factory=_Assets)


async def test_pdf_acquisition_returns_existing_download_metadata_shape(tmp_path: Path) -> None:
    download = CanvusContentDownloader(_Client(), output_dir=tmp_path)
    meta = await download.acquire("canvas-a", "pdf", "pdf-1")
    assert meta["canvas_id"] == "canvas-a"
    assert meta["widget_id"] == "pdf-1"
    assert meta["widget_type"] == "Pdf"
    assert meta["mime_type"] == "application/pdf"
    assert len(meta["sha256"]) == 64
    assert Path(meta["path"]).is_file()


async def test_acquisition_rejects_non_canvus_identifiers_and_unknown_sources(tmp_path: Path) -> None:
    download = CanvusContentDownloader(_Client(), output_dir=tmp_path)
    with pytest.raises(SourceRequestError, match="invalid_source"):
        await download.acquire("canvas-a", "pdf", "../../etc/passwd")
    with pytest.raises(SourceRequestError, match="invalid_source"):
        await download.acquire("canvas-a", "unsupported", "source-1")
