"""Fail-closed normalization for untrusted data at the model boundary."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

FIXED_ERROR = {"error": "tool_result_unavailable", "data_classification": "unknown"}
FIXED_ERROR_JSON = json.dumps(FIXED_ERROR, separators=(",", ":"))
_SENSITIVE_WORDS = frozenset({
    "auth", "authorization", "bearer", "cookie", "credential", "password", "secret", "token",
    "traceback", "stack", "stderr", "exception", "error",
})
_ASSIGNMENT = re.compile(
    r"\b(?:[a-z0-9]+[ _-]?)?(?:token|api[ _-]?key|credential|password|private[ _-]?key|secret)\b\s*(?:=|:\s*\S)",
    re.I,
)
_AUTHORIZATION = re.compile(r"\b(?:proxy-)?authorization\s*:\s*\S", re.I)
_COOKIE = re.compile(r"\b(?:set-)?cookie\s*(?:[:=])\s*[^;\s=]+\s*=", re.I)
_PEM = re.compile(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----", re.I)
_DIAGNOSTIC = re.compile(
    r"\b(?:traceback|stack[ _-]?trace|(?:mcp|provider)\s+(?:raw\s+)?(?:error|detail|exception|traceback|stack|stderr)|(?:fatal|unhandled)\s+(?:error|exception))\b",
    re.I,
)
_URL = re.compile(r"(?:https?|file)://[^\s\"'<>]+", re.I)
_LOCAL_PATH = re.compile(r"(?<![:/\w])(?:~[\\/]|\.{1,2}[\\/]|/[A-Za-z0-9._-]+(?:[\\/][A-Za-z0-9._-]+)*|[A-Za-z]:[\\/])")


@dataclass(frozen=True)
class ResultLimits:
    """Independent bounds applied before data reaches a model or ledger."""

    max_depth: int = 8
    max_containers: int = 256
    max_string_chars: int = 8192
    max_bytes: int = 32 * 1024
    max_items: int = 256

    def __post_init__(self) -> None:
        if min(self.max_depth, self.max_containers, self.max_string_chars, self.max_bytes, self.max_items) < 1:
            raise ValueError("result limits must be positive")
        if self.max_string_chars > self.max_bytes:
            raise ValueError("individual string limit exceeds aggregate byte limit")


class UnsafeResultError(ValueError):
    """A result is malformed, oversized, or unsafe for a model transcript."""


def fixed_error_json() -> str:
    """Return the sole safe error shape for rejected tool data."""
    return FIXED_ERROR_JSON


def encoded_size(value: object) -> int:
    """Return compact UTF-8 JSON size for an already-safe value."""
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8"))


def sanitize_text(value: object, limits: ResultLimits | None = None) -> str | None:
    """Return one bounded, non-sensitive model text value or ``None``."""
    limits = limits or ResultLimits()
    if not isinstance(value, str) or len(value) > limits.max_string_chars:
        return None
    try:
        return value if len(value.encode("utf-8")) <= limits.max_bytes and not is_unsafe_text(value) else None
    except UnicodeEncodeError:
        return None


def sanitize_result(raw: str, limits: ResultLimits) -> dict[str, Any] | None:
    """Parse one MCP object or reject it without reflecting untrusted detail."""
    try:
        if len(raw.encode("utf-8")) > limits.max_bytes:
            raise UnsafeResultError
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise UnsafeResultError
        value = _visit(parsed, 1, [0], limits)
        if not isinstance(value, dict) or encoded_size(value) > limits.max_bytes or _item_count(value) > limits.max_items:
            raise UnsafeResultError
        return value
    except (TypeError, ValueError, UnicodeEncodeError):
        return None


def is_unsafe_data(value: object, limits: ResultLimits | None = None) -> bool:
    """Return whether arbitrary tool arguments are unsafe or exceed their bounds."""
    try:
        limits = limits or ResultLimits()
        safe = _visit(value, 1, [0], limits)
        return encoded_size(safe) > limits.max_bytes or _item_count(safe) > limits.max_items
    except (TypeError, ValueError, UnicodeEncodeError):
        return True


def _visit(value: Any, depth: int, containers: list[int], limits: ResultLimits) -> Any:
    if depth > limits.max_depth:
        raise UnsafeResultError
    if isinstance(value, dict):
        _count(containers, limits)
        safe: dict[str, Any] = {}
        for key, item in value.items():
            safe_key = _safe_key(key, limits)
            safe_value = _visit(item, depth + 1, containers, limits)
            if not _drop_key(safe_key):
                safe[safe_key] = safe_value
        return safe
    if isinstance(value, list):
        _count(containers, limits)
        return [_visit(item, depth + 1, containers, limits) for item in value]
    if isinstance(value, str):
        if len(value) > limits.max_string_chars or is_unsafe_text(value):
            raise UnsafeResultError
        return value
    if isinstance(value, float) and not math.isfinite(value):
        raise UnsafeResultError
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise UnsafeResultError


def _count(containers: list[int], limits: ResultLimits) -> None:
    containers[0] += 1
    if containers[0] > limits.max_containers:
        raise UnsafeResultError


def _item_count(value: object) -> int:
    if isinstance(value, dict):
        return len(value) + sum(_item_count(item) for item in value.values())
    if isinstance(value, list):
        return len(value) + sum(_item_count(item) for item in value)
    return 1


def _safe_key(key: object, limits: ResultLimits) -> str:
    if not isinstance(key, str) or len(key) > limits.max_string_chars or _sensitive_key(key):
        raise UnsafeResultError
    return key


def _drop_key(key: str) -> bool:
    return any(word in {"url", "uri", "href", "path", "cache", "capability", "file"} for word in _key_words(key))


def _sensitive_key(key: str) -> bool:
    words = _key_words(key)
    compact = "".join(words)
    return any(word in _SENSITIVE_WORDS for word in words) or any(
        marker in compact for marker in ("accesstoken", "refreshtoken", "apikey", "privatekey", "rawerror", "rawdetail")
    )


def _key_words(key: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", key).lower())


def is_unsafe_text(value: str) -> bool:
    """Detect credential material, diagnostics, capability URLs, and local paths."""
    return bool(
        value.lower().lstrip().startswith("bearer ") or _AUTHORIZATION.search(value)
        or _COOKIE.search(value) or _ASSIGNMENT.search(value)
        or _PEM.search(value) or _DIAGNOSTIC.search(value) or _LOCAL_PATH.search(value)
        or _has_capability_url(value)
    )


def _has_capability_url(value: str) -> bool:
    for match in _URL.finditer(value):
        parsed = urlsplit(unquote(match.group(0)))
        if parsed.scheme.lower() == "file" or parsed.username or parsed.password:
            return True
        if any(_sensitive_key(key) or key.lower() in {"sig", "signature", "capability"} for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
            return True
    return False


__all__ = [
    "FIXED_ERROR", "FIXED_ERROR_JSON", "ResultLimits", "encoded_size", "fixed_error_json", "is_unsafe_data",
    "is_unsafe_text", "sanitize_result", "sanitize_text",
]
