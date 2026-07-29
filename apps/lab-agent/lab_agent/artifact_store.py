"""Tenant-bound facade for canonical, versioned generated artifacts."""

from __future__ import annotations

import secrets
import sqlite3
import time
from typing import Any

from lab_agent.artifact_capabilities import ArtifactCapabilityMixin
from lab_agent.artifact_composition import (
    _compose_document,
    _create_and_compose,
    _get_document,
    _get_or_create_and_compose,
    _list_versions,
    _prepare_version_fields,
)
from lab_agent.models.artifact import (
    ArtifactDocument,
    ArtifactMetadata,
    ArtifactProvenance,
    ArtifactType,
    ArtifactVersion,
)
from lab_agent.models.states import DecisionState
from lab_agent.state import artifact_tokens, artifact_widgets
from lab_agent.state import artifacts as artifacts_repo
from lab_agent.state.artifact_idempotency import ArtifactIdempotencyConflictError
from lab_agent.state.artifact_widgets import ArtifactWidgetConflictError
from lab_agent.state.artifacts import (
    ArtifactAlreadyExistsError,
    ArtifactCanvasScopeError,
    ArtifactNotFoundError,
)
from lab_agent.state.models import Clock
from lab_agent.tenant import TenantContext

__all__ = [
    "ArtifactAlreadyExistsError",
    "ArtifactCanvasScopeError",
    "ArtifactIdempotencyConflictError",
    "ArtifactNotFoundError",
    "ArtifactStore",
    "ArtifactWidgetConflictError",
]
_OPAQUE_ID_BYTES = _IDEMPOTENCY_KEY_BYTES = 16


class ArtifactStore(ArtifactCapabilityMixin):
    """Artifact ledger bound to one tenant and, optionally, its canvas allowlist."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        clock: Clock = time.time,
        tenant_context: TenantContext | None = None,
    ) -> None:
        self.conn = conn
        self.clock = clock
        self._configure_tenant_scope(tenant_context)

    def create_artifact(
        self,
        *,
        canvas_id: str,
        artifact_type: ArtifactType,
        state: DecisionState,
        payload: dict[str, Any],
        provenance: ArtifactProvenance,
        metadata: ArtifactMetadata | None = None,
        round: int = 0,
        idempotency_key: str | None = None,
    ) -> ArtifactDocument:
        self._require_canvas_scope(canvas_id)
        key = idempotency_key or secrets.token_urlsafe(_IDEMPOTENCY_KEY_BYTES)
        return _create_and_compose(
            self.conn,
            clock=self.clock,
            tenant_id=self.tenant_id,
            opaque_id=secrets.token_urlsafe(_OPAQUE_ID_BYTES),
            idempotency_key=key,
            canvas_id=canvas_id,
            artifact_type=artifact_type,
            state=state,
            round=round,
            payload=payload,
            metadata=metadata,
            provenance=provenance,
        )

    def get_or_create_artifact(
        self,
        *,
        canvas_id: str,
        idempotency_key: str,
        artifact_type: ArtifactType,
        state: DecisionState,
        payload: dict[str, Any],
        provenance: ArtifactProvenance,
        metadata: ArtifactMetadata | None = None,
        round: int = 0,
    ) -> ArtifactDocument:
        self._require_canvas_scope(canvas_id)
        return _get_or_create_and_compose(
            self.conn,
            clock=self.clock,
            tenant_id=self.tenant_id,
            opaque_id=secrets.token_urlsafe(_OPAQUE_ID_BYTES),
            idempotency_key=idempotency_key,
            canvas_id=canvas_id,
            artifact_type=artifact_type,
            state=state,
            round=round,
            payload=payload,
            metadata=metadata,
            provenance=provenance,
        )

    def get_artifact(self, opaque_id: str, *, canvas_id: str) -> ArtifactDocument | None:
        self._require_canvas_scope(canvas_id)
        return _get_document(
            self.conn, tenant_id=self.tenant_id, opaque_id=opaque_id, canvas_id=canvas_id
        )

    def get_artifact_by_widget(self, *, canvas_id: str, widget_id: str) -> ArtifactDocument | None:
        self._require_canvas_scope(canvas_id)
        mapping = artifact_widgets.get_by_widget(
            self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id, widget_id=widget_id
        )
        return self.get_artifact(mapping.opaque_id, canvas_id=canvas_id) if mapping else None

    def get_authorized_artifact(self, opaque_id: str, *, token: str) -> ArtifactDocument | None:
        resolved = artifact_tokens.verify_token_any_canvas(
            self.conn, tenant_id=self.tenant_id, opaque_id=opaque_id, token=token
        )
        if resolved is None:
            return None
        _tenant_id, canvas_id = resolved
        try:
            self._require_canvas_scope(canvas_id)
        except (ValueError, PermissionError):
            return None
        return _get_document(
            self.conn, tenant_id=self.tenant_id, opaque_id=opaque_id, canvas_id=canvas_id
        )

    def append_version(
        self,
        opaque_id: str,
        *,
        canvas_id: str,
        state: DecisionState,
        payload: dict[str, Any],
        provenance: ArtifactProvenance,
        metadata: ArtifactMetadata | None = None,
        round: int | None = None,
    ) -> ArtifactDocument:
        self._require_canvas_scope(canvas_id)
        metadata_dict, provenance_dict, content_hash = _prepare_version_fields(
            payload, metadata, provenance
        )
        record = artifacts_repo.append_version(
            self.conn,
            clock=self.clock,
            tenant_id=self.tenant_id,
            opaque_id=opaque_id,
            canvas_id=canvas_id,
            state=state.value,
            round=round,
            payload=payload,
            metadata=metadata_dict,
            provenance=provenance_dict,
            content_hash=content_hash,
        )
        version = artifacts_repo.get_version(
            self.conn, tenant_id=self.tenant_id, opaque_id=opaque_id, version=record.current_version
        )
        assert version is not None
        widget = artifact_widgets.get_mapping(
            self.conn, tenant_id=self.tenant_id, opaque_id=opaque_id
        )
        return _compose_document(record, version, widget_id=widget.widget_id if widget else None)

    def list_versions(self, opaque_id: str, *, canvas_id: str) -> list[ArtifactVersion]:
        self._require_canvas_scope(canvas_id)
        return _list_versions(
            self.conn, tenant_id=self.tenant_id, opaque_id=opaque_id, canvas_id=canvas_id
        )
