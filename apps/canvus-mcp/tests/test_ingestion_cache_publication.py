"""Atomic content-addressed cache publication regressions."""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from canvus_mcp.ingestion_cache import AssetCache


def test_concurrent_identical_writers_converge_on_verified_immutable_entry(tmp_path: Path) -> None:
    cache = AssetCache(tmp_path / "cache")
    data = b"same verified bytes" * 1024
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            digests = list(pool.map(cache.store_bytes, [data] * 16))
        digest = hashlib.sha256(data).hexdigest()
        assert digests == [digest] * 16
        assert cache.read(digest) == data
        assert list((tmp_path / "cache" / digest[:2]).glob(".*.tmp")) == []
    finally:
        cache.close()


def test_incomplete_legacy_final_entry_is_repaired_without_replacing_good_collision(tmp_path: Path) -> None:
    cache = AssetCache(tmp_path / "cache")
    data = b"recover this entry"
    digest = hashlib.sha256(data).hexdigest()
    try:
        bucket = tmp_path / "cache" / digest[:2]
        bucket.mkdir(exist_ok=True)
        os.chmod(bucket, 0o700)
        partial = bucket / digest
        partial.write_bytes(b"")
        os.chmod(partial, 0o600)

        assert cache.store_bytes(data) == digest
        assert cache.read(digest) == data
        assert cache.store_bytes(data) == digest
        assert cache.read(digest) == data
    finally:
        cache.close()
