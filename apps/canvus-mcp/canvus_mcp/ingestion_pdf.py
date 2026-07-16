"""Resource-limited PDF page extraction for untrusted ingestion inputs."""

from __future__ import annotations

import multiprocessing
import os
from io import BytesIO
from multiprocessing.connection import Connection
from typing import Literal

from pypdf import PdfReader

PdfStatus = Literal["ok", "encrypted", "malformed", "oversized"]
_TIMEOUT_SECONDS = 5.0


def extract_pdf_page(
    data: bytes, *, index: int, password: str | None, max_pages: int, max_output_chars: int,
    max_source_bytes: int,
) -> tuple[PdfStatus, str]:
    """Extract at most one page in an isolated, memory-limited child process."""
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_extract_child,
        args=(sender, data, index, password, max_pages, max_output_chars, max_source_bytes),
    )
    process.start()
    sender.close()
    try:
        if not receiver.poll(_TIMEOUT_SECONDS):
            return "oversized", ""
        try:
            status, text = receiver.recv()
        except EOFError:
            return "oversized", ""
        if status != "ok" or len(text) > max_output_chars:
            return status if status != "ok" else "oversized", ""
        return "ok", text
    finally:
        receiver.close()
        process.join(timeout=0.1)
        if process.is_alive():
            process.terminate()
            process.join()
        process.close()


def _extract_child(
    sender: Connection, data: bytes, index: int, password: str | None,
    max_pages: int, max_output_chars: int, max_source_bytes: int,
) -> None:
    try:
        _apply_limits(max_source_bytes, max_output_chars)
        reader = PdfReader(BytesIO(data), strict=False)
        if reader.is_encrypted and (not password or reader.decrypt(password) == 0):
            sender.send(("encrypted", ""))
            return
        if len(reader.pages) > max_pages or index >= len(reader.pages):
            sender.send(("oversized" if len(reader.pages) > max_pages else "malformed", ""))
            return
        text = reader.pages[index].extract_text() or ""
        sender.send(("oversized", "") if len(text) > max_output_chars else ("ok", text))
    except MemoryError:
        sender.send(("oversized", ""))
    except Exception:
        sender.send(("malformed", ""))
    finally:
        sender.close()


def _apply_limits(max_source_bytes: int, max_output_chars: int) -> None:
    """Contain decompression and parser allocation in the child on POSIX hosts."""
    if os.name != "posix":
        return
    import resource

    memory_limit = min(512 * 1024 * 1024, max(128 * 1024 * 1024, max_source_bytes * 8, max_output_chars * 64))
    resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))


__all__ = ["extract_pdf_page"]
