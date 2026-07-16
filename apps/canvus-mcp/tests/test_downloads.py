"""Tests for the download-to-disk helper."""

from __future__ import annotations

from pathlib import Path

from canvus_mcp.downloads import save_bytes, sniff_mime

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PDF = b"%PDF-1.7\n" + b"x" * 32


def test_sniff_mime_prefers_declared():
    mime, _ = sniff_mime(PNG, filename="x.jpg", declared="image/png")
    assert mime == "image/png"


def test_sniff_mime_uses_filename():
    mime, suffix = sniff_mime(b"whatever", filename="report.pdf")
    assert mime == "application/pdf"
    assert suffix == ".pdf"


def test_sniff_mime_magic_bytes():
    assert sniff_mime(PNG)[0] == "image/png"
    assert sniff_mime(PDF)[0] == "application/pdf"


def test_sniff_mime_fallback_octet_stream():
    assert sniff_mime(b"\x00\x01\x02")[0] == "application/octet-stream"


def test_save_bytes_writes_file_and_metadata(tmp_path: Path):
    meta = save_bytes(PDF, str(tmp_path), stem="pdf_123")
    saved = Path(meta["path"])
    assert saved.exists()
    assert saved.read_bytes() == PDF
    assert meta["mime_type"] == "application/pdf"
    assert saved.suffix == ".pdf"
    assert meta["size_bytes"] == len(PDF)
    assert len(meta["sha256"]) == 64


def test_save_bytes_sanitizes_stem(tmp_path: Path):
    meta = save_bytes(PNG, str(tmp_path), stem="../../etc/passwd")
    saved = Path(meta["path"])
    # Must stay inside the output dir (no path traversal via the stem).
    assert saved.parent == tmp_path.resolve()
