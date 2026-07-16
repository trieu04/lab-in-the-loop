"""Public facade for canonical, versioned generated artifacts."""

from __future__ import annotations

import secrets
import sqlite3
import time
from typing import Any

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

__all__ = [
    "ArtifactAlreadyExistsError",
    "ArtifactCanvasScopeError",
    "ArtifactIdempotencyConflictError",
    "ArtifactNotFoundError",
    "ArtifactStore",
    "ArtifactWidgetConflictError",
]

_OPAQUE_ID_BYTES = 16
_IDEMPOTENCY_KEY_BYTES = 16


class ArtifactStore:
    """Canonical, versioned generated-artifact ledger for one lab-agent process."""

    def __init__(self, conn: sqlite3.Connection, *, clock: Clock = time.time) -> None:
        self.conn = conn
        self.clock = clock

    # ── documents ──────────────────────────────────────────────────────
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
        """Persist version 1 with a generated opaque identifier."""
        key = (
            idempotency_key
            if idempotency_key is not None
            else secrets.token_urlsafe(_IDEMPOTENCY_KEY_BYTES)
        )
        return _create_and_compose(
            self.conn,
            clock=self.clock,
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
        """Return the existing keyed artifact, or atomically create version 1.

        Reusing a key for another artifact type raises a conflict.
        """
        return _get_or_create_and_compose(
            self.conn,
            clock=self.clock,
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
        """Return the current document only within its recorded canvas."""
        return _get_document(self.conn, opaque_id=opaque_id, canvas_id=canvas_id)

    def get_artifact_by_widget(self, *, canvas_id: str, widget_id: str) -> ArtifactDocument | None:
        mapping = artifact_widgets.get_by_widget(self.conn, canvas_id=canvas_id, widget_id=widget_id)
        if mapping is None:
            return None
        return self.get_artifact(mapping.opaque_id, canvas_id=canvas_id)

    def get_authorized_artifact(self, opaque_id: str, *, token: str) -> ArtifactDocument | None:
        """Server route for ``/artifacts/{opaque_id}?token=...`` (no
        ``canvas_id`` in the URL): resolves it internally from the active
        token row, so callers never need raw SQL. ``None`` -- never raises --
        for an unknown id or a wrong/guessed/revoked/rotated token alike."""
        canvas_id = artifact_tokens.verify_token_any_canvas(self.conn, opaque_id=opaque_id, token=token)
        if canvas_id is None:
            return None
        return self.get_artifact(opaque_id, canvas_id=canvas_id)

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
        """Append a new immutable version; never overwrites prior history.
        Raises on an unknown id/canvas mismatch (internal path, fails loud)."""
        metadata_dict, provenance_dict, content_hash = _prepare_version_fields(payload, metadata, provenance)
        record = artifacts_repo.append_version(
            self.conn,
            clock=self.clock,
            opaque_id=opaque_id,
            canvas_id=canvas_id,
            state=state.value,
            round=round,
            payload=payload,
            metadata=metadata_dict,
            provenance=provenance_dict,
            content_hash=content_hash,
        )
        version = artifacts_repo.get_version(self.conn, opaque_id=opaque_id, version=record.current_version)
        assert version is not None  # just written above, same transaction committed
        widget = artifact_widgets.get_mapping(self.conn, opaque_id=opaque_id)
        return _compose_document(record, version, widget_id=widget.widget_id if widget else None)

    def list_versions(self, opaque_id: str, *, canvas_id: str) -> list[ArtifactVersion]:
        """Return append-only history, oldest first, within one canvas."""
        return _list_versions(self.conn, opaque_id=opaque_id, canvas_id=canvas_id)

    # ── Browser widget mapping ─────────────────────────────────────────
    def map_widget(self, opaque_id: str, *, canvas_id: str, widget_id: str) -> None:
        """Idempotent restart-safe mapping; see
        :func:`lab_agent.state.artifact_widgets.map_widget`."""
        artifact_widgets.map_widget(
            self.conn, clock=self.clock, opaque_id=opaque_id, canvas_id=canvas_id, widget_id=widget_id
        )

    # ── capability tokens (never exposed with their hash) ──────────────
    def issue_token(self, opaque_id: str, *, canvas_id: str) -> str:
        """Returns the raw bearer token exactly once; only its hash persists."""
        return artifact_tokens.issue_token(self.conn, clock=self.clock, opaque_id=opaque_id, canvas_id=canvas_id)

    def verify_token(self, opaque_id: str, *, canvas_id: str, token: str) -> bool:
        """Adversary-facing check: ``False`` for unknown/wrong-canvas/revoked/
        rotated/guessed tokens alike -- never raises, never distinguishes."""
        return artifact_tokens.verify_token(self.conn, opaque_id=opaque_id, canvas_id=canvas_id, token=token)

    def rotate_token(self, opaque_id: str, *, canvas_id: str) -> str:
        """Atomically revoke the current active token and mint a replacement."""
        return artifact_tokens.rotate_token(self.conn, clock=self.clock, opaque_id=opaque_id, canvas_id=canvas_id)

    def revoke_token(self, opaque_id: str, *, canvas_id: str) -> None:
        artifact_tokens.revoke_token(self.conn, clock=self.clock, opaque_id=opaque_id, canvas_id=canvas_id)
