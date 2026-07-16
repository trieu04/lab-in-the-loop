"""Immutable, descriptor-confined SHA-256 raw-byte cache for ingestion."""

from __future__ import annotations

import fcntl
import hashlib
import os
import secrets
import stat
from pathlib import Path

from canvus_mcp.downloads import private_directory
from canvus_mcp.ingestion_validation import is_sha256

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW
_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW


class CacheIntegrityError(RuntimeError):
    """The cache path, bytes, or caller-provided digest is not trustworthy."""


class AssetCache:
    """Stores verified immutable bytes under ``root/<prefix>/<sha256>``."""

    def __init__(self, root: Path) -> None:
        if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
            raise CacheIntegrityError("secure cache descriptors are unavailable")
        try:
            self.root, self._root_fd = private_directory(root)
        except (OSError, ValueError) as error:
            raise CacheIntegrityError("cache root is not private") from error

    def close(self) -> None:
        """Release the pinned root descriptor exactly once."""
        descriptor, self._root_fd = self._root_fd, -1
        if descriptor >= 0:
            os.close(descriptor)

    def store_bytes(self, data: bytes) -> str:
        """Publish verified bytes atomically, converging concurrent writers."""
        digest = hashlib.sha256(data).hexdigest()
        bucket = self._bucket(digest, create=True)
        temp: str | None = None
        try:
            fcntl.flock(bucket, fcntl.LOCK_EX)
            try:
                if self._existing_matches(bucket, digest, data):
                    return digest
                self._remove_incomplete_entry(bucket, digest)
                temp = f".{digest}.{secrets.token_hex(12)}.tmp"
                self._write_temp(bucket, temp, data, digest)
                try:
                    os.link(temp, digest, src_dir_fd=bucket, dst_dir_fd=bucket, follow_symlinks=False)
                except FileExistsError:
                    if not self._existing_matches(bucket, digest, data):
                        raise CacheIntegrityError("existing cache entry has different bytes")
                os.fsync(bucket)
                return digest
            finally:
                if temp is not None:
                    _unlink_if_exists(temp, bucket)
                fcntl.flock(bucket, fcntl.LOCK_UN)
        finally:
            os.close(bucket)

    def import_file(self, path: Path, *, expected_sha256: str) -> str:
        """Copy a regular, non-symlink source only after digest verification."""
        try:
            descriptor = os.open(path, _READ_FLAGS)
        except OSError as error:
            raise CacheIntegrityError("source must be a regular non-symlink file") from error
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise CacheIntegrityError("source must be a regular non-symlink file")
            data = source.read()
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise CacheIntegrityError("source SHA-256 mismatch")
        return self.store_bytes(data)

    def read(self, digest: str) -> bytes:
        """Read only an intact regular entry below the pinned cache root."""
        bucket = self._bucket(digest, create=False)
        try:
            return self._read_entry(bucket, digest)
        finally:
            os.close(bucket)

    def _write_temp(self, bucket: int, temp: str, data: bytes, digest: str) -> None:
        descriptor = os.open(temp, _WRITE_FLAGS, 0o600, dir_fd=bucket)
        try:
            with os.fdopen(descriptor, "wb") as output:
                descriptor = -1
                os.fchmod(output.fileno(), 0o600)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            if self._read_entry(bucket, temp, expected_digest=digest) != data:
                raise CacheIntegrityError("temporary cache verification failed")
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _existing_matches(self, bucket: int, digest: str, data: bytes) -> bool:
        try:
            return self._read_entry(bucket, digest) == data
        except CacheIntegrityError:
            return False

    def _remove_incomplete_entry(self, bucket: int, digest: str) -> None:
        try:
            self._read_entry(bucket, digest)
        except CacheIntegrityError:
            try:
                os.unlink(digest, dir_fd=bucket)
            except FileNotFoundError:
                pass
        else:
            raise CacheIntegrityError("existing cache entry has different bytes")

    def _bucket(self, digest: str, *, create: bool) -> int:
        if self._root_fd < 0 or not is_sha256(digest):
            raise CacheIntegrityError("invalid SHA-256")
        created = False
        if create:
            try:
                os.mkdir(digest[:2], 0o700, dir_fd=self._root_fd)
                created = True
            except FileExistsError:
                pass
        descriptor = -1
        try:
            descriptor = os.open(digest[:2], _DIR_FLAGS, dir_fd=self._root_fd)
            if created:
                os.fchmod(descriptor, 0o700)
            info = os.fstat(descriptor)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise CacheIntegrityError("cache bucket is not private")
            return descriptor
        except OSError as error:
            raise CacheIntegrityError("cache bucket is not a real directory") from error
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            raise

    def _read_entry(self, bucket: int, name: str, *, expected_digest: str | None = None) -> bytes:
        digest = expected_digest or name
        try:
            before = os.stat(name, dir_fd=bucket, follow_symlinks=False)
            descriptor = os.open(name, _READ_FLAGS, dir_fd=bucket)
        except OSError as error:
            raise CacheIntegrityError("cache entry cannot be safely read") from error
        with os.fdopen(descriptor, "rb") as source:
            after = os.fstat(source.fileno())
            if not _is_private_regular(before, after):
                raise CacheIntegrityError("cache entry is not a private regular file")
            data = source.read()
        if hashlib.sha256(data).hexdigest() != digest:
            raise CacheIntegrityError("cache entry digest mismatch")
        return data


def _is_private_regular(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        stat.S_ISREG(after.st_mode)
        and after.st_uid == os.geteuid()
        and stat.S_IMODE(after.st_mode) == 0o600
        and (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
    )


def _unlink_if_exists(name: str, bucket: int) -> None:
    try:
        os.unlink(name, dir_fd=bucket)
    except FileNotFoundError:
        pass


__all__ = ["AssetCache", "CacheIntegrityError"]
