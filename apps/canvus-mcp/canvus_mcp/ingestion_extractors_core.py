"""Deterministic bounded local parsers for approved ingestion modalities."""

from __future__ import annotations

import csv
import json
import os
import stat
from dataclasses import dataclass
from io import BytesIO, StringIO
from pathlib import Path
from typing import Protocol

from pypdf import PdfReader

from canvus_mcp.ingestion_pdf import extract_pdf_page
from canvus_mcp.ingestion_types import (
    ExtractedChunk,
    ExtractionResult,
    ExtractionStatus,
    UnitSpec,
    failed_extraction_result,
)


@dataclass(frozen=True)
class ExtractorLimits:
    max_source_bytes: int = 10 * 1024 * 1024
    max_output_chars: int = 16_000
    max_chunk_chars: int = 4_000
    max_records: int = 500
    max_pdf_pages: int = 200
    max_metadata_entries: int = 50
    pdf_password_file: Path | None = None


class Extractor(Protocol):
    def plan(self, data: bytes, mime_type: str) -> tuple[UnitSpec, ...]: ...

    def extract(self, data: bytes, mime_type: str, spec: UnitSpec) -> ExtractionResult: ...


class LocalExtractor:
    """No-network parsers that emit only bounded text or image metadata."""

    def __init__(self, limits: ExtractorLimits = ExtractorLimits()) -> None:
        values = (limits.max_source_bytes, limits.max_output_chars, limits.max_chunk_chars,
                  limits.max_records, limits.max_pdf_pages)
        if min(values) < 1:
            raise ValueError("extractor limits must be positive")
        self.limits = limits

    def plan(self, data: bytes, mime_type: str) -> tuple[UnitSpec, ...]:
        if mime_type != "application/pdf" or len(data) > self.limits.max_source_bytes:
            return (UnitSpec("whole", 0),)
        try:
            reader = PdfReader(BytesIO(data), strict=False)
            if reader.is_encrypted or len(reader.pages) > self.limits.max_pdf_pages:
                return (UnitSpec("whole", 0),)
            return tuple(UnitSpec("pdf_page", i, i, i + 1) for i in range(len(reader.pages))) or (UnitSpec("whole", 0),)
        except Exception:
            return (UnitSpec("whole", 0),)

    def extract(self, data: bytes, mime_type: str, spec: UnitSpec) -> ExtractionResult:
        if len(data) > self.limits.max_source_bytes:
            return failed_extraction_result(ExtractionStatus.OVERSIZED)
        if mime_type == "application/pdf":
            return self._pdf(data, spec)
        if mime_type.startswith("text/") and mime_type not in {"text/csv", "text/tab-separated-values"}:
            return self._text(data, spec)
        if mime_type in {"text/csv", "text/tab-separated-values"}:
            return self._table(data, spec, "\t" if mime_type.endswith("tab-separated-values") else ",")
        if mime_type in {"application/json", "text/json"}:
            return self._json(data, spec)
        if mime_type in {"image/png", "image/jpeg", "image/gif"}:
            return self._image(data, mime_type, spec)
        return failed_extraction_result(ExtractionStatus.UNSUPPORTED)

    def _text(self, data: bytes, spec: UnitSpec) -> ExtractionResult:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return failed_extraction_result(ExtractionStatus.MALFORMED)
        return self._chunks(text, spec, {"kind": "text"})

    def _table(self, data: bytes, spec: UnitSpec, delimiter: str) -> ExtractionResult:
        try:
            reader = csv.reader(StringIO(data.decode("utf-8")), delimiter=delimiter, strict=True)
            rows: list[str] = []
            total = 0
            has_more = False
            for index, row in enumerate(reader):
                if any(len(field) > self.limits.max_output_chars for field in row):
                    return failed_extraction_result(ExtractionStatus.OVERSIZED)
                if index >= self.limits.max_records:
                    has_more = True
                    continue
                rendered = "\t".join(row)
                total += len(rendered) + bool(rows)
                if total > self.limits.max_output_chars:
                    return failed_extraction_result(ExtractionStatus.OVERSIZED)
                rows.append(rendered)
        except (UnicodeDecodeError, csv.Error):
            return failed_extraction_result(ExtractionStatus.MALFORMED)
        return self._chunks("\n".join(rows), spec, {"kind": "table", "records": len(rows), "has_more": has_more})

    def _json(self, data: bytes, spec: UnitSpec) -> ExtractionResult:
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return failed_extraction_result(ExtractionStatus.MALFORMED)
        records = parsed if isinstance(parsed, list) else [parsed]
        rendered = [json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records[: self.limits.max_records]]
        return self._chunks("\n".join(rendered), spec, {"kind": "json", "records": len(rendered)})

    def _pdf(self, data: bytes, spec: UnitSpec) -> ExtractionResult:
        index = spec.start if spec.kind == "pdf_page" else 0
        status, text = extract_pdf_page(
            data, index=index, password=self._password(), max_pages=self.limits.max_pdf_pages,
            max_output_chars=self.limits.max_output_chars, max_source_bytes=self.limits.max_source_bytes,
        )
        if status != "ok":
            return failed_extraction_result(ExtractionStatus(status))
        return self._chunks(text, spec, {"kind": "pdf", "page": index + 1})

    def _image(self, data: bytes, mime_type: str, spec: UnitSpec) -> ExtractionResult:
        dimensions = _image_dimensions(data, mime_type)
        if dimensions is None:
            return failed_extraction_result(ExtractionStatus.MALFORMED)
        width, height = dimensions
        chunk = ExtractedChunk(spec.ordinal * 1_000_000, "", {"format": mime_type[6:], "width": width, "height": height})
        return ExtractionResult(ExtractionStatus.OK, (chunk,))

    def _chunks(self, text: str, spec: UnitSpec, metadata: dict[str, str | int]) -> ExtractionResult:
        bounded = text[: self.limits.max_output_chars]
        chunks = tuple(ExtractedChunk(spec.ordinal * 1_000_000 + i, bounded[offset:offset + self.limits.max_chunk_chars], metadata)
                       for i, offset in enumerate(range(0, len(bounded), self.limits.max_chunk_chars)))
        return ExtractionResult(ExtractionStatus.OK, chunks)

    def _password(self) -> str | None:
        path = self.limits.pdf_password_file
        if path is None or path.is_symlink():
            return None
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 256:
                    return None
                data = handle.read(257)
            return data.decode("utf-8").strip() or None if len(data) <= 256 else None
        except (OSError, UnicodeDecodeError):
            return None


def _image_dimensions(data: bytes, mime_type: str) -> tuple[int, int] | None:
    if mime_type == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if mime_type == "image/gif" and data[:6] in {b"GIF87a", b"GIF89a"} and len(data) >= 10:
        return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    if mime_type == "image/jpeg" and data.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 9 <= len(data):
            if data[offset] != 0xFF:
                return None
            marker, length = data[offset + 1], int.from_bytes(data[offset + 2:offset + 4], "big")
            if marker in {0xC0, 0xC1, 0xC2} and length >= 7:
                return int.from_bytes(data[offset + 7:offset + 9], "big"), int.from_bytes(data[offset + 5:offset + 7], "big")
            if length < 2:
                return None
            offset += length + 2
    return None
