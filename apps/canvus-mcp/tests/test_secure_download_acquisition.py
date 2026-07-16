"""Regression tests for bounded, private Canvus content acquisition."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from canvus_mcp.access_control import AccessPolicy, Role
from canvus_mcp.content_download import CanvusContentDownloader, SourceRequestError
from canvus_mcp.ingestion_cache import AssetCache
from canvus_mcp.ingestion_pipeline import IngestionPipeline
from canvus_mcp.ingestion_store import IngestionStore
from canvus_mcp.tools import ingestion


@dataclass
class _Widget:
    original_filename: str = "report.pdf"
    mime_type: str = "application/pdf"


class _Stream:
    def __init__(self, chunks: list[bytes], content_length: int | None = None) -> None:
        self.headers = {} if content_length is None else {"content-length": str(content_length)}
        self._chunks = chunks
        self.started = False

    async def __aenter__(self) -> _Stream:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        self.started = True
        for chunk in self._chunks:
            yield chunk


@dataclass
class _Pdfs:
    async def get(self, _canvas_id: str, _source_id: str) -> _Widget:
        return _Widget()

    async def download(self, _canvas_id: str, _source_id: str) -> bytes:
        return b"%PDF-1.7 legacy"


@dataclass
class _Widgets:
    pdfs: _Pdfs = field(default_factory=_Pdfs)


@dataclass
class _Client:
    stream: _Stream | None = None
    widgets: _Widgets = field(default_factory=_Widgets)

    def stream_download(self, _path: str, *, headers: dict[str, str]) -> _Stream:
        assert headers == {}
        assert self.stream is not None
        return self.stream


class _MCP:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *_args: Any, **_kwargs: Any):
        def register(function: Any) -> Any:
            self.tools[function.__name__] = function
            return function

        return register


async def test_oversized_stream_cleans_temp_without_persisting_output(tmp_path: Path) -> None:
    client = _Client(stream=_Stream([b"%PDF-", b"too-large"], content_length=None))
    download = CanvusContentDownloader(client, output_dir=tmp_path)

    with pytest.raises(ValueError, match="source_too_large"):
        await download.acquire("canvas-a", "pdf", "pdf-1", max_bytes=8)

    assert list(tmp_path.iterdir()) == []


async def test_content_length_preflight_aborts_before_stream_or_directory_creation(tmp_path: Path) -> None:
    stream = _Stream([b"small"], content_length=9)
    with pytest.raises(ValueError, match="source_too_large"):
        await CanvusContentDownloader(_Client(stream), output_dir=tmp_path / "downloads").acquire(
            "canvas-a", "pdf", "pdf-1", max_bytes=8
        )

    assert not stream.started
    assert not (tmp_path / "downloads").exists()


async def test_streamed_acquisitions_keep_metadata_bound_to_unique_files(tmp_path: Path) -> None:
    first = await CanvusContentDownloader(
        _Client(_Stream([b"%PDF-first"])), output_dir=tmp_path
    ).acquire("canvas-a", "pdf", "pdf-1", max_bytes=80)
    second = await CanvusContentDownloader(
        _Client(_Stream([b"%PDF-second"])), output_dir=tmp_path
    ).acquire("canvas-a", "pdf", "pdf-1", max_bytes=80)

    assert first["path"] != second["path"]
    assert Path(first["path"]).read_bytes() == b"%PDF-first"
    assert Path(second["path"]).read_bytes() == b"%PDF-second"


async def test_oversized_ingestion_creates_no_file_cache_asset_or_job(tmp_path: Path) -> None:
    store = IngestionStore(tmp_path / "ingestion.db")
    cache = AssetCache(tmp_path / "cache")
    mcp = _MCP()
    policy = AccessPolicy(
        store=store, reader_token=None, trusted_service_token=SecretStr("service-token"),
        operator_token=None, reader_canvases=(), trusted_service_canvases=("canvas-a",),
        operator_canvases=(), stdio_role=Role.READER, stdio_canvases=(),
    )
    ingestion.register(
        mcp, policy=policy, pipeline=IngestionPipeline(store=store, cache=cache), max_chunk_chars=80,
        max_source_bytes=8,
        downloader=CanvusContentDownloader(_Client(_Stream([b"%PDF-", b"too-large"])), output_dir=tmp_path / "downloads"),
    )
    context = SimpleNamespace(request_context=SimpleNamespace(
        request=SimpleNamespace(headers=[("authorization", "Bearer service-token")])
    ))
    try:
        with pytest.raises(ValueError, match="source_too_large"):
            await mcp.tools["enqueue_ingestion"]("canvas-a", "pdf", "pdf-1", ctx=context)
        assert list((tmp_path / "downloads").iterdir()) == []
        assert list((tmp_path / "cache").iterdir()) == []
        assert store.conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
        assert store.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally:
        store.close()
        cache.close()


async def test_download_rejects_existing_insecure_storage_root(tmp_path: Path) -> None:
    output = tmp_path / "downloads"
    output.mkdir(mode=0o755)

    with pytest.raises(ValueError, match="private storage directory is unsafe"):
        await CanvusContentDownloader(_Client(), output_dir=output).acquire("canvas-a", "pdf", "legacy-1")


def test_download_storage_uses_exact_private_permissions_under_permissive_umask(tmp_path: Path) -> None:
    output = tmp_path / "downloads"
    old_umask = os.umask(0)
    try:
        metadata = asyncio.run(
            CanvusContentDownloader(_Client(), output_dir=output).acquire("canvas-a", "pdf", "legacy-1")
        )
    finally:
        os.umask(old_umask)

    assert (output.stat().st_mode & 0o777) == 0o700
    assert (Path(metadata["path"]).stat().st_mode & 0o777) == 0o600


@pytest.mark.parametrize(
    "source_id",
    ["pdf-1", "d9428888-122b-11e1-b85c-61cd3cbb3210", "asset:9f2e3a", "a" * 64],
)
async def test_legacy_canvus_identifier_formats_remain_accepted(tmp_path: Path, source_id: str) -> None:
    metadata = await CanvusContentDownloader(_Client(), output_dir=tmp_path).acquire(
        "canvas-a", "pdf", source_id
    )
    assert metadata["widget_id"] == source_id


@pytest.mark.parametrize("source_id", ["../asset", "asset/name", "asset\x00id", "asset\nid", ""])
async def test_source_identifier_rejects_traversal_and_control_characters(
    tmp_path: Path, source_id: str
) -> None:
    with pytest.raises(SourceRequestError, match="invalid_source"):
        await CanvusContentDownloader(_Client(), output_dir=tmp_path).acquire("canvas-a", "pdf", source_id)
