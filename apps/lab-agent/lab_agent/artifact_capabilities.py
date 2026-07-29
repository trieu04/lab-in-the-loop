"""Capability-token and Browser-widget methods mixed into ``ArtifactStore``."""

from __future__ import annotations

import sqlite3

from lab_agent.state import artifact_tokens, artifact_widgets
from lab_agent.state.models import Clock
from lab_agent.state.tenant_scope import TenantScopeStoreMixin


class ArtifactCapabilityMixin(TenantScopeStoreMixin):
    """Tenant-qualified capability and Browser mapping persistence."""

    conn: sqlite3.Connection
    clock: Clock

    def map_widget(self, opaque_id: str, *, canvas_id: str, widget_id: str) -> None:
        self._require_canvas_scope(canvas_id)
        artifact_widgets.map_widget(
            self.conn,
            clock=self.clock,
            tenant_id=self.tenant_id,
            opaque_id=opaque_id,
            canvas_id=canvas_id,
            widget_id=widget_id,
        )

    def issue_token(self, opaque_id: str, *, canvas_id: str) -> str:
        self._require_canvas_scope(canvas_id)
        return artifact_tokens.issue_token(
            self.conn,
            clock=self.clock,
            tenant_id=self.tenant_id,
            opaque_id=opaque_id,
            canvas_id=canvas_id,
        )

    def verify_token(self, opaque_id: str, *, canvas_id: str, token: str) -> bool:
        try:
            self._require_canvas_scope(canvas_id)
        except (ValueError, PermissionError):
            return False
        return artifact_tokens.verify_token(
            self.conn,
            tenant_id=self.tenant_id,
            opaque_id=opaque_id,
            canvas_id=canvas_id,
            token=token,
        )

    def rotate_token(self, opaque_id: str, *, canvas_id: str) -> str:
        self._require_canvas_scope(canvas_id)
        return artifact_tokens.rotate_token(
            self.conn,
            clock=self.clock,
            tenant_id=self.tenant_id,
            opaque_id=opaque_id,
            canvas_id=canvas_id,
        )

    def revoke_token(self, opaque_id: str, *, canvas_id: str) -> None:
        self._require_canvas_scope(canvas_id)
        artifact_tokens.revoke_token(
            self.conn,
            clock=self.clock,
            tenant_id=self.tenant_id,
            opaque_id=opaque_id,
            canvas_id=canvas_id,
        )


__all__ = ["ArtifactCapabilityMixin"]
