"""Runtime wiring and secret-safe extractor regressions."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from pypdf import PdfWriter

from canvus_mcp import server
from canvus_mcp.config import Settings
from canvus_mcp.extractors import LocalExtractor
from canvus_mcp.ingestion_types import ExtractionStatus, UnitSpec


def _settings(tmp_path: Path, password_file: Path | None = None) -> Settings:
    return Settings(
        api_url="https://canvus.example/api/v1", api_key="sdk-secret",
        mcp_ingestion_db_path=str(tmp_path / "ingestion.db"),
        mcp_ingestion_cache_dir=str(tmp_path / "cache"),
        mcp_ingestion_max_source_bytes=123, mcp_ingestion_max_output_chars=88,
        mcp_ingestion_chunk_char_cap=44, mcp_ingestion_max_records=3,
        mcp_ingestion_max_pdf_pages=2,
        mcp_ingestion_pdf_password_file=str(password_file) if password_file else None,
    )


def test_runtime_wires_all_extractor_limits(tmp_path: Path) -> None:
    runtime = server.build_ingestion_runtime(_settings(tmp_path))
    try:
        extractor = runtime.pipeline.extractor
        assert isinstance(extractor, LocalExtractor)
        assert extractor.limits.max_source_bytes == 123
        assert extractor.limits.max_output_chars == 88
        assert extractor.limits.max_chunk_chars == 44
        assert extractor.limits.max_records == 3
        assert extractor.limits.max_pdf_pages == 2
    finally:
        runtime.store.close()


def test_password_file_is_restrictive_and_unsafe_file_stays_encrypted(tmp_path: Path) -> None:
    password = tmp_path / "password"
    password.write_text("secret", encoding="utf-8")
    password.chmod(0o644)
    writer, buffer = PdfWriter(), BytesIO()
    writer.add_blank_page(width=10, height=10)
    writer.encrypt("secret")
    writer.write(buffer)
    cfg = _settings(tmp_path, password).model_copy(update={"mcp_ingestion_max_source_bytes": 10_000})
    runtime = server.build_ingestion_runtime(cfg)
    try:
        result = runtime.pipeline.extractor.extract(buffer.getvalue(), "application/pdf", UnitSpec("whole", 0))
        assert result.status is ExtractionStatus.ENCRYPTED
    finally:
        runtime.store.close()


def test_restrictive_password_file_unlocks_encrypted_pdf(tmp_path: Path) -> None:
    password = tmp_path / "password"
    password.write_text("secret", encoding="utf-8")
    password.chmod(0o600)
    writer, buffer = PdfWriter(), BytesIO()
    writer.add_blank_page(width=10, height=10)
    writer.encrypt("secret")
    writer.write(buffer)
    cfg = _settings(tmp_path, password).model_copy(update={"mcp_ingestion_max_source_bytes": 10_000})
    runtime = server.build_ingestion_runtime(cfg)
    try:
        result = runtime.pipeline.extractor.extract(buffer.getvalue(), "application/pdf", UnitSpec("whole", 0))
        assert result.status is ExtractionStatus.OK
    finally:
        runtime.store.close()
