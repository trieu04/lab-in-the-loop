"""Row conversion and canonical JSON encoding for the artifact ledger."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from lab_agent.state.models import ArtifactRecord, ArtifactVersionRecord

_JSON_KWARGS: dict[str, Any] = {"sort_keys": True, "separators": (",", ":"), "ensure_ascii": True}


def _dump(value: dict[str, Any]) -> str:
    return json.dumps(value, **_JSON_KWARGS)


def _row_to_record(row: sqlite3.Row) -> ArtifactRecord:
    return ArtifactRecord(
        tenant_id=row["tenant_id"], opaque_id=row["opaque_id"], canvas_id=row["canvas_id"],
        idempotency_key=row["idempotency_key"], artifact_type=row["artifact_type"],
        state=row["state"], round=row["round"], current_version=row["current_version"],
        content_hash=row["content_hash"], created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _row_to_version(row: sqlite3.Row) -> ArtifactVersionRecord:
    return ArtifactVersionRecord(
        tenant_id=row["tenant_id"], opaque_id=row["opaque_id"], version=row["version"],
        payload=json.loads(row["payload_json"]), metadata=json.loads(row["metadata_json"]),
        provenance=json.loads(row["provenance_json"]), content_hash=row["content_hash"],
        created_at=row["created_at"],
    )


__all__ = ["_dump", "_row_to_record", "_row_to_version"]
