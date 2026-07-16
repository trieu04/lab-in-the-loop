"""Persist downloaded content to disk and describe it for tool responses.

Binary downloads (PDF/image/video bytes) are written under the configured
output directory rather than returned inline, keeping MCP responses small and
safe for large files.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
from pathlib import Path
from typing import Any

# Minimal magic-byte sniffing for the common Canvus asset types, used when the
# widget model carries no ``mime_type`` and the filename has no useful suffix.
_MAGIC: tuple[tuple[bytes, str, str], ...] = (
    (b"%PDF-", "application/pdf", ".pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
    (b"RIFF", "image/webp", ".webp"),  # WEBP (RIFF container); good enough here
)

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(name: str) -> str:
    """Reduce an arbitrary filename to a filesystem-safe basename."""
    base = Path(name).name
    cleaned = _SAFE.sub("_", base).strip("._")
    return cleaned or "download"


def sniff_mime(data: bytes, *, filename: str = "", declared: str = "") -> tuple[str, str]:
    """Return ``(mime_type, suffix)`` for ``data``.

    Precedence: a declared mime type, then the filename suffix, then magic
    bytes, then an octet-stream fallback.
    """
    if declared:
        suffix = mimetypes.guess_extension(declared.split(";")[0].strip()) or ""
        if not suffix and filename:
            suffix = Path(filename).suffix
        return declared, suffix
    if filename:
        guessed, _ = mimetypes.guess_type(filename)
        if guessed:
            return guessed, Path(filename).suffix
    for magic, mime, suffix in _MAGIC:
        if data.startswith(magic):
            return mime, suffix
    return "application/octet-stream", Path(filename).suffix or ".bin"


def save_bytes(
    data: bytes,
    output_dir: str,
    *,
    stem: str,
    filename: str = "",
    declared_mime: str = "",
) -> dict[str, Any]:
    """Write ``data`` to ``output_dir`` and return path/mime/size/hash metadata.

    The saved filename is ``{stem}{suffix}`` where the suffix is derived from
    the resolved mime type or original filename.
    """
    mime, suffix = sniff_mime(data, filename=filename, declared=declared_mime)
    if filename and not suffix:
        suffix = Path(filename).suffix
    out_dir = Path(output_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_safe_name(stem)}{suffix}"
    path.write_bytes(data)
    return {
        "path": str(path.resolve()),
        "mime_type": mime,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "original_filename": filename or None,
    }


__all__ = ["save_bytes", "sniff_mime"]
