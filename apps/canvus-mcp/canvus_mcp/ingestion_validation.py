"""Shared validation helpers for ingestion integrity values."""

from __future__ import annotations

import re

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EXTRACTOR_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")


def is_sha256(value: str) -> bool:
    """Return whether ``value`` is a lowercase hexadecimal SHA-256 digest."""
    return _SHA256.fullmatch(value) is not None


def is_extractor_version(value: object) -> bool:
    """Return whether a version is a small ASCII-safe derived-work identifier."""
    return isinstance(value, str) and _EXTRACTOR_VERSION.fullmatch(value) is not None


__all__ = ["is_extractor_version", "is_sha256"]
