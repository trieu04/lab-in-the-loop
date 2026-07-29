"""Composition helpers for :mod:`lab_agent.artifact_store`: canonical content
hashing and assembling a public
:class:`~lab_agent.models.artifact.ArtifactDocument` from the internal
SQL-layer records. Split out so ``ArtifactStore`` itself stays a thin,
readable delegator -- same "sibling split module, import the private helper
directly" idiom used by the state repositories.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from lab_agent.models.artifact import (
    ArtifactDocument,
    ArtifactMetadata,
    ArtifactProvenance,
    ArtifactType,
    ArtifactVersion,
)
from lab_agent.models.states import DecisionState
from lab_agent.state import artifact_idempotency, artifact_widgets
from lab_agent.state import artifacts as artifacts_repo
from lab_agent.state.models import ArtifactRecord, ArtifactVersionRecord, Clock


def _content_hash(
    payload: dict[str, Any], metadata: dict[str, Any], provenance: dict[str, Any]
) -> str:
    canonical = json.dumps(
        {"payload": payload, "metadata": metadata, "provenance": provenance},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _prepare_version_fields(
    payload: dict[str, Any], metadata: ArtifactMetadata | None, provenance: ArtifactProvenance
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Metadata dict, provenance dict, and content hash shared by every
    ArtifactStore method that writes a version row (create/append/get-or-create)."""
    metadata_dict = (metadata or ArtifactMetadata()).model_dump()
    provenance_dict = provenance.model_dump()
    return metadata_dict, provenance_dict, _content_hash(payload, metadata_dict, provenance_dict)


def _compose_document(
    record: ArtifactRecord, version: ArtifactVersionRecord, *, widget_id: str | None
) -> ArtifactDocument:
    return ArtifactDocument(
        tenant_id=record.tenant_id,
        opaque_id=record.opaque_id,
        canvas_id=record.canvas_id,
        artifact_type=ArtifactType(record.artifact_type),
        state=DecisionState(record.state),
        round=record.round,
        current_version=record.current_version,
        payload=version.payload,
        metadata=ArtifactMetadata(**version.metadata),
        provenance=ArtifactProvenance(**version.provenance),
        content_hash=record.content_hash,
        widget_id=widget_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _get_document(
    conn: sqlite3.Connection, *, tenant_id: str, opaque_id: str, canvas_id: str
) -> ArtifactDocument | None:
    record = artifacts_repo.get_artifact(conn, tenant_id=tenant_id, opaque_id=opaque_id)
    if record is None or record.canvas_id != canvas_id:
        return None
    version = artifacts_repo.get_version(
        conn, tenant_id=tenant_id, opaque_id=opaque_id, version=record.current_version
    )
    if version is None:  # pragma: no cover - every artifact has a version
        return None
    widget = artifact_widgets.get_mapping(conn, tenant_id=tenant_id, opaque_id=opaque_id)
    return _compose_document(record, version, widget_id=widget.widget_id if widget else None)


def _list_versions(
    conn: sqlite3.Connection, *, tenant_id: str, opaque_id: str, canvas_id: str
) -> list[ArtifactVersion]:
    record = artifacts_repo.get_artifact(conn, tenant_id=tenant_id, opaque_id=opaque_id)
    if record is None or record.canvas_id != canvas_id:
        return []
    return [
        ArtifactVersion(
            tenant_id=tenant_id,
            canvas_id=record.canvas_id,
            opaque_id=opaque_id,
            version=item.version,
            payload=item.payload,
            metadata=ArtifactMetadata(**item.metadata),
            provenance=ArtifactProvenance(**item.provenance),
            content_hash=item.content_hash,
            created_at=item.created_at,
        )
        for item in artifacts_repo.list_versions(conn, tenant_id=tenant_id, opaque_id=opaque_id)
    ]


def _create_and_compose(
    conn: sqlite3.Connection,
    *,
    clock: Clock,
    tenant_id: str,
    opaque_id: str,
    idempotency_key: str,
    canvas_id: str,
    artifact_type: ArtifactType,
    state: DecisionState,
    round: int,
    payload: dict[str, Any],
    metadata: ArtifactMetadata | None,
    provenance: ArtifactProvenance,
) -> ArtifactDocument:
    """Shared by ``ArtifactStore.create_artifact``: resolve metadata/provenance/
    hash, persist unconditionally, then compose. Never checks for an existing
    idempotency key -- see :func:`_get_or_create_and_compose` for the
    restart-idempotent path."""
    metadata_dict, provenance_dict, content_hash = _prepare_version_fields(
        payload, metadata, provenance
    )
    record = artifacts_repo.create_artifact(
        conn,
        clock=clock,
        tenant_id=tenant_id,
        opaque_id=opaque_id,
        idempotency_key=idempotency_key,
        canvas_id=canvas_id,
        artifact_type=artifact_type.value,
        state=state.value,
        round=round,
        payload=payload,
        metadata=metadata_dict,
        provenance=provenance_dict,
        content_hash=content_hash,
    )
    version = artifacts_repo.get_version(
        conn, tenant_id=tenant_id, opaque_id=opaque_id, version=record.current_version
    )
    assert version is not None  # just written above, same transaction committed
    return _compose_document(record, version, widget_id=None)


def _get_or_create_and_compose(
    conn: sqlite3.Connection,
    *,
    clock: Clock,
    tenant_id: str,
    opaque_id: str,
    idempotency_key: str,
    canvas_id: str,
    artifact_type: ArtifactType,
    state: DecisionState,
    round: int,
    payload: dict[str, Any],
    metadata: ArtifactMetadata | None,
    provenance: ArtifactProvenance,
) -> ArtifactDocument:
    """Shared by ``ArtifactStore.get_or_create_artifact``: resolve the
    metadata/provenance/hash fields, delegate to the atomic state-layer
    get-or-create, then compose whichever record it returned (existing or
    freshly created) into a public document, widget mapping included."""
    metadata_dict, provenance_dict, content_hash = _prepare_version_fields(
        payload, metadata, provenance
    )
    record, _created = artifact_idempotency.get_or_create_artifact(
        conn,
        clock=clock,
        tenant_id=tenant_id,
        opaque_id=opaque_id,
        idempotency_key=idempotency_key,
        canvas_id=canvas_id,
        artifact_type=artifact_type.value,
        state=state.value,
        round=round,
        payload=payload,
        metadata=metadata_dict,
        provenance=provenance_dict,
        content_hash=content_hash,
    )
    version = artifacts_repo.get_version(
        conn, tenant_id=tenant_id, opaque_id=record.opaque_id, version=record.current_version
    )
    assert version is not None  # invariant: every artifact row has >=1 version
    widget = artifact_widgets.get_mapping(conn, tenant_id=tenant_id, opaque_id=record.opaque_id)
    return _compose_document(record, version, widget_id=widget.widget_id if widget else None)


__all__ = ["_compose_document", "_content_hash", "_create_and_compose", "_get_document",
           "_get_or_create_and_compose", "_list_versions", "_prepare_version_fields"]
