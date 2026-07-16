"""MIME propagation and immutable legacy-download regression tests."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from canvus_mcp import content_download
from canvus_mcp.content_download import CanvusContentDownloader
from canvus_mcp.downloads import save_bytes
from canvus_mcp.extractors import LocalExtractor
from canvus_mcp.ingestion_types import ExtractionStatus, UnitSpec


class _Response:
    headers = {"content-type": "Text/Plain; charset=utf-8"}

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        yield b"plain source"


class _Client:
    widgets = object()
    assets = object()


async def test_streamed_asset_preserves_normalized_response_content_type(tmp_path: Path, monkeypatch) -> None:
    @asynccontextmanager
    async def stream(_client: object, _path: str, *, headers: dict[str, str]):
        assert headers == {"canvas-id": "canvas-a"}
        yield _Response()

    monkeypatch.setattr(content_download, "stream_download", stream)
    downloader = CanvusContentDownloader(_Client(), output_dir=tmp_path)
    metadata = await downloader.acquire("canvas-a", "asset", "asset-a", max_bytes=1024)
    assert metadata["mime_type"] == "text/plain"


@pytest.mark.parametrize(
    ("header", "payload"),
    [
        ("text/plain", b"plain source"),
        ("text/csv; charset=utf-8", b"name,value\\na,1\\n"),
        ("text/tab-separated-values", b"name\\tvalue\\na\\t1\\n"),
        ("Application/JSON; Charset=UTF-8", b'{"name": "value"}'),
    ],
)
async def test_asset_mime_reaches_the_matching_local_extractor(
    tmp_path: Path, monkeypatch, header: str, payload: bytes
) -> None:
    class Response:
        headers = {"Content-Type": header}

        async def aiter_bytes(self) -> AsyncIterator[bytes]:
            yield payload

    @asynccontextmanager
    async def stream(_client: object, _path: str, *, headers: dict[str, str]):
        yield Response()

    monkeypatch.setattr(content_download, "stream_download", stream)
    meta = await CanvusContentDownloader(_Client(), output_dir=tmp_path).acquire(
        "canvas-a", "asset", "asset-a", max_bytes=1024
    )
    result = LocalExtractor().extract(Path(meta["path"]).read_bytes(), meta["mime_type"], UnitSpec("whole", 0))
    assert result.status is ExtractionStatus.OK


def test_buffered_download_paths_are_immutable_under_same_stem_concurrency(tmp_path: Path) -> None:
    first = save_bytes(b"first", str(tmp_path), stem="mutable", filename="source.txt")
    second = save_bytes(b"second", str(tmp_path), stem="mutable", filename="source.txt")

    assert first["path"] != second["path"]
    assert Path(first["path"]).read_bytes() == b"first"
    assert Path(second["path"]).read_bytes() == b"second"
    assert first["sha256"] == hashlib.sha256(b"first").hexdigest()


def test_buffered_download_same_stem_concurrent_results_keep_their_hash_bindings(tmp_path: Path) -> None:
    data = [f"payload-{number}".encode() for number in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda value: save_bytes(value, str(tmp_path), stem="shared", filename="source.txt"),
                data,
            )
        )
    for result, value in zip(results, data, strict=True):
        assert Path(result["path"]).read_bytes() == value
        assert result["sha256"] == hashlib.sha256(value).hexdigest()
