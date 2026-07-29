"""Versioned canonical hash encodings for immutable audit events."""

from __future__ import annotations

import hashlib
import json

GENESIS_HASH = "0" * 64


def hash_v1(
    sequence: int, canvas_id: str, event: str, payload_json: str, previous_hash: str
) -> str:
    """Return the original delimiter-based audit hash without alteration."""
    digest = hashlib.sha256()
    for part in (str(sequence), canvas_id, event, payload_json, previous_hash):
        digest.update(part.encode("utf-8"))
        digest.update(b"|")
    return digest.hexdigest()


def hash_v2(
    sequence: int,
    tenant_id: str,
    canvas_id: str,
    round_index: int | None,
    event: str,
    payload_json: str,
    previous_hash: str,
    created_at: float,
) -> str:
    """Hash the complete tenant-qualified v2 event canonical representation."""
    canonical = json.dumps(
        [
            2,
            sequence,
            tenant_id,
            canvas_id,
            round_index,
            event,
            payload_json,
            previous_hash,
            created_at,
        ],
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = ["GENESIS_HASH", "hash_v1", "hash_v2"]
