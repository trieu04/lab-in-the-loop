"""Private, atomic persistence for Canvus binary downloads."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import secrets
import stat
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

_MAGIC: tuple[tuple[bytes, str, str], ...] = (
    (b"%PDF-", "application/pdf", ".pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
    (b"RIFF", "image/webp", ".webp"),
)
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_MIME = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,63}$")
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW


class SourceTooLargeError(ValueError):
    """A streamed source exceeded the caller's strict byte limit."""

    def __init__(self) -> None:
        super().__init__("source_too_large")


def private_directory(path: str | Path) -> tuple[Path, int]:
    """Open a private directory without following path-component symlinks."""
    target = Path(os.path.abspath(os.fspath(Path(path).expanduser())))
    descriptor = os.open("/", _DIR_FLAGS)
    try:
        for component in target.parts[1:]:
            created = False
            try:
                os.mkdir(component, 0o700, dir_fd=descriptor)
                created = True
            except FileExistsError:
                pass
            child = os.open(component, _DIR_FLAGS, dir_fd=descriptor)
            try:
                if created:
                    os.fchmod(child, 0o700)
            except BaseException:
                os.close(child)
                raise
            previous = descriptor
            descriptor = child
            os.close(previous)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise ValueError("private storage directory is unsafe")
    except BaseException:
        os.close(descriptor)
        raise
    return target, descriptor


def _safe_name(name: str) -> str:
    base = Path(name).name
    cleaned = _SAFE.sub("_", base).strip("._")
    return cleaned or "download"


def sniff_mime(data: bytes, *, filename: str = "", declared: str = "") -> tuple[str, str]:
    """Return canonical ``(mime_type, suffix)`` from safe metadata or bytes."""
    if mime := normalize_mime(declared):
        suffix = mimetypes.guess_extension(mime) or ""
        return mime, suffix or Path(filename).suffix
    if filename:
        guessed, _ = mimetypes.guess_type(filename)
        if guessed:
            return guessed, Path(filename).suffix
    for magic, mime, suffix in _MAGIC:
        if data.startswith(magic):
            return mime, suffix
    return "application/octet-stream", Path(filename).suffix or ".bin"


def normalize_mime(value: object) -> str:
    """Normalize an untrusted HTTP media type, dropping parameters safely."""
    if not isinstance(value, str) or len(value) > 128:
        return ""
    media_type = value.split(";", 1)[0].strip().lower()
    return media_type if _MIME.fullmatch(media_type) else ""


def _metadata(path: Path, data: bytes, filename: str, declared_mime: str) -> dict[str, Any]:
    mime, _ = sniff_mime(data, filename=filename, declared=declared_mime)
    return {
        "path": str(path),
        "mime_type": mime,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "original_filename": filename or None,
    }


def save_bytes(
    data: bytes, output_dir: str, *, stem: str, filename: str = "", declared_mime: str = ""
) -> dict[str, Any]:
    """Publish buffered content to a unique immutable path and hash binding."""
    mime, suffix = sniff_mime(data, filename=filename, declared=declared_mime)
    root, root_fd = private_directory(output_dir)
    name = f"{_safe_name(stem)}.{secrets.token_hex(12)}{suffix}"
    descriptor = -1
    try:
        descriptor = os.open(name, _FILE_FLAGS | os.O_EXCL, 0o600, dir_fd=root_fd)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            os.fchmod(output.fileno(), 0o600)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.fsync(root_fd)
        return {**_metadata(root / name, data, filename, declared_mime), "mime_type": mime}
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(name, dir_fd=root_fd)
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(root_fd)


async def save_stream(
    chunks: AsyncIterator[bytes], output_dir: str, *, stem: str, filename: str = "",
    declared_mime: str = "", max_bytes: int
) -> dict[str, Any]:
    """Persist a bounded stream to a temporary private file then promote it."""
    root, root_fd = private_directory(output_dir)
    sample = bytearray()
    digest = hashlib.sha256()
    total = 0
    token = secrets.token_hex(12)
    temp = f".{_safe_name(stem)}.{token}.tmp"
    descriptor = -1
    try:
        descriptor = os.open(temp, _FILE_FLAGS | os.O_EXCL, 0o600, dir_fd=root_fd)
        output = os.fdopen(descriptor, "wb")
        descriptor = -1
        with output:
            async for chunk in chunks:
                if not isinstance(chunk, bytes):
                    raise ValueError("invalid_download_stream")
                total += len(chunk)
                if total > max_bytes:
                    raise SourceTooLargeError()
                output.write(chunk)
                digest.update(chunk)
                if len(sample) < 16:
                    sample.extend(chunk[: 16 - len(sample)])
            output.flush()
            os.fchmod(output.fileno(), 0o600)
            os.fsync(output.fileno())
        mime, suffix = sniff_mime(bytes(sample), filename=filename, declared=declared_mime)
        name = f"{_safe_name(stem)}.{token}{suffix}"
        os.replace(temp, name, src_dir_fd=root_fd, dst_dir_fd=root_fd)
        return {
            "path": str(root / name), "mime_type": mime, "size_bytes": total,
            "sha256": digest.hexdigest(), "original_filename": filename or None,
        }
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temp, dir_fd=root_fd)
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(root_fd)


__all__ = ["SourceTooLargeError", "normalize_mime", "private_directory", "save_bytes", "save_stream", "sniff_mime"]
