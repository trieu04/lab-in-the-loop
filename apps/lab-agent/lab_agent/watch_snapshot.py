"""Helpers for reading normalized watcher snapshots."""

from typing import Literal

ExecutionMode = Literal["manual", "auto"]


def _items(snapshot: dict[str, object], key: str) -> list[dict[str, object]]:
    value = snapshot.get(key, [])
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _mode(item: dict[str, object]) -> ExecutionMode | None:
    value = item.get("execution_mode", "manual")
    return value if value in ("manual", "auto") else None


__all__ = ["ExecutionMode", "_items", "_mode"]
