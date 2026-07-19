"""Bounded deterministic extraction tests using local bytes only."""

from __future__ import annotations

from io import BytesIO

from pypdf import PdfWriter

from canvus_mcp.extractors import ExtractorLimits, LocalExtractor
from canvus_mcp.ingestion_types import ExtractionStatus, UnitSpec


def make_text_pdf(text: str) -> bytes:
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >> /MediaBox [0 0 612 792] /Contents 4 0 R >>",
        f"<< /Length {len(f'BT /F1 12 Tf 72 720 Td ({text}) Tj ET')} >>\nstream\nBT /F1 12 Tf 72 720 Td ({text}) Tj ET\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = "%PDF-1.4\n"
    offsets = [0]
    for index, body in enumerate(objects, 1):
        offsets.append(len(output.encode()))
        output += f"{index} 0 obj\n{body}\nendobj\n"
    xref = len(output.encode())
    output += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    output += "".join(f"{offset:010} 00000 n \n" for offset in offsets[1:])
    return (output + f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode()


def test_text_tables_json_and_image_metadata_are_bounded() -> None:
    extractor = LocalExtractor(ExtractorLimits(max_output_chars=8, max_chunk_chars=4, max_records=1))
    text = extractor.extract(b"abcdefghij", "text/plain", UnitSpec("whole", 0))
    csv = extractor.extract(b"a,b\n1,2\n3,4\n", "text/csv", UnitSpec("whole", 0))
    json_result = extractor.extract(b'[{"a": 1}, {"b": 2}]', "application/json", UnitSpec("whole", 0))
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (3).to_bytes(4, "big") + (2).to_bytes(4, "big")
    image = extractor.extract(png, "image/png", UnitSpec("whole", 0))
    gif = extractor.extract(b"GIF89a" + (4).to_bytes(2, "little") + (5).to_bytes(2, "little"), "image/gif", UnitSpec("whole", 0))
    jpeg = extractor.extract(b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x02\x00\x03", "image/jpeg", UnitSpec("whole", 0))

    assert [chunk.text for chunk in text.chunks] == ["abcd", "efgh"]
    assert len(csv.chunks) == 1
    assert "".join(chunk.text for chunk in json_result.chunks) == '{"a": 1}'
    assert image.chunks[0].metadata == {"format": "png", "height": 2, "width": 3}
    assert gif.chunks[0].metadata["width"] == 4
    assert jpeg.chunks[0].metadata["height"] == 2


def test_text_and_csv_invalid_utf8_and_malformed_quotes_are_typed() -> None:
    extractor = LocalExtractor()
    assert extractor.extract(b"\xff", "text/plain", UnitSpec("whole", 0)).status is ExtractionStatus.MALFORMED
    assert extractor.extract(b"a,b\n\"unterminated", "text/csv", UnitSpec("whole", 0)).status is ExtractionStatus.MALFORMED


def test_csv_streams_only_bounded_records_and_chars() -> None:
    extractor = LocalExtractor(ExtractorLimits(max_records=2, max_output_chars=8, max_chunk_chars=4))
    dense = (b"a,b\n" * 50_000)
    result = extractor.extract(dense, "text/csv", UnitSpec("whole", 0))
    assert result.status is ExtractionStatus.OK
    assert "".join(chunk.text for chunk in result.chunks) == "a\tb\na\tb"
    assert result.chunks[0].metadata["records"] == 2
    assert result.chunks[0].metadata["has_more"] is True


def test_csv_validates_syntax_after_retained_record_limit() -> None:
    extractor = LocalExtractor(ExtractorLimits(max_records=1))
    result = extractor.extract(b"first\nsecond\n\"unterminated", "text/csv", UnitSpec("whole", 0))
    assert result.status is ExtractionStatus.MALFORMED


def test_csv_rejects_oversized_field_without_truncating_it() -> None:
    extractor = LocalExtractor(ExtractorLimits(max_output_chars=4, max_chunk_chars=4))
    result = extractor.extract(b"field-too-large,ok\n", "text/csv", UnitSpec("whole", 0))
    assert result.status is ExtractionStatus.OVERSIZED


def test_pdf_output_over_cap_is_typed_oversized_without_publishing_chunks() -> None:
    extractor = LocalExtractor(ExtractorLimits(max_source_bytes=50_000, max_output_chars=32, max_chunk_chars=16))
    result = extractor.extract(make_text_pdf("x" * 1_000), "application/pdf", UnitSpec("whole", 0))
    assert result.status is ExtractionStatus.OVERSIZED
    assert result.chunks == ()


def test_pdf_page_text_and_typed_bad_input_states() -> None:
    extractor = LocalExtractor(ExtractorLimits(max_source_bytes=2048, max_pdf_pages=2))
    pdf = make_text_pdf("hello")
    planned = extractor.plan(pdf, "application/pdf")
    result = extractor.extract(pdf, "application/pdf", planned[0])
    encrypted = PdfWriter()
    encrypted.add_blank_page(width=10, height=10)
    encrypted.encrypt("secret")
    buffer = BytesIO()
    encrypted.write(buffer)

    assert result.status is ExtractionStatus.OK
    assert "hello" in result.chunks[0].text
    assert extractor.extract(buffer.getvalue(), "application/pdf", UnitSpec("whole", 0)).status is ExtractionStatus.ENCRYPTED
    assert extractor.extract(b"not a pdf", "application/pdf", UnitSpec("whole", 0)).status is ExtractionStatus.MALFORMED
    assert extractor.extract(b"x" * 2049, "text/plain", UnitSpec("whole", 0)).status is ExtractionStatus.OVERSIZED
    assert extractor.extract(b"video", "video/mp4", UnitSpec("whole", 0)).status is ExtractionStatus.UNSUPPORTED
