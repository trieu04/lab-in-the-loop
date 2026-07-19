"""Security regressions for the descriptor-confined ingestion byte cache."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from canvus_mcp.ingestion_cache import AssetCache, CacheIntegrityError


def test_cache_creates_exact_private_directory_and_file_modes(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    cache = AssetCache(root)
    old_umask = os.umask(0)
    try:
        digest = cache.store_bytes(b"private cache bytes")
    finally:
        os.umask(old_umask)
        cache.close()

    entry = root / digest[:2] / digest
    assert (root.stat().st_mode & 0o777) == 0o700
    assert (entry.parent.stat().st_mode & 0o777) == 0o700
    assert (entry.stat().st_mode & 0o777) == 0o600


def test_cache_rejects_an_existing_insecure_root(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    root.mkdir(mode=0o755)

    with pytest.raises(CacheIntegrityError, match="private"):
        AssetCache(root)


def test_replaced_prefix_cannot_redirect_cache_write_outside_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "cache"
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    cache = AssetCache(root)
    digest = hashlib.sha256(b"race target").hexdigest()
    prefix = root / digest[:2]
    original_open = os.open
    raced = False

    def replace_prefix(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes], flags: int, mode: int = 0o777,
        *, dir_fd: int | None = None
    ) -> int:
        nonlocal raced
        if path == digest[:2] and dir_fd is not None and not raced:
            raced = True
            prefix.rmdir()
            prefix.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", replace_prefix)
    try:
        with pytest.raises(CacheIntegrityError):
            cache.store_bytes(b"race target")
    finally:
        cache.close()

    assert raced
    assert not (outside / digest).exists()
