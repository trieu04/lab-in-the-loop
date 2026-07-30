"""Tenant- and canvas-scoped StateStore facade for side-effect intents."""

from __future__ import annotations

import sqlite3

from lab_agent.state import intents
from lab_agent.state.models import Clock, SideEffectIntent


class IntentStoreMixin:
    """Bind all intent reads, claims, and updates to one tenant and canvas."""

    conn: sqlite3.Connection
    clock: Clock
    tenant_id: str

    def _require_canvas_scope(self, canvas_id: str) -> None: ...
    def _allowed_canvas_ids(self) -> tuple[str, ...] | None: ...

    def prepare_intent(
        self, *, idempotency_key: str, canvas_id: str, kind: str, input_hash: str,
    ) -> SideEffectIntent:
        self._require_canvas_scope(canvas_id)
        return intents.prepare_intent(
            self.conn, clock=self.clock, tenant_id=self.tenant_id, canvas_id=canvas_id,
            idempotency_key=idempotency_key, kind=kind, input_hash=input_hash,
        )

    def _scoped_intent(self, idempotency_key: str, canvas_id: str) -> SideEffectIntent | None:
        self._require_canvas_scope(canvas_id)
        return intents.get_intent(
            self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id,
            idempotency_key=idempotency_key,
        )

    def _legacy_canvas(self, idempotency_key: str) -> str:
        record = self.get_intent(idempotency_key)
        if record is None:
            raise RuntimeError(f"intent {idempotency_key!r} not found")
        return record.canvas_id

    def mark_intent_submitted(self, idempotency_key: str, *, canvas_id: str | None = None) -> SideEffectIntent:
        canvas_id = canvas_id or self._legacy_canvas(idempotency_key)
        self._scoped_intent(idempotency_key, canvas_id)
        return intents.mark_submitted(
            self.conn, clock=self.clock, tenant_id=self.tenant_id, canvas_id=canvas_id,
            idempotency_key=idempotency_key,
        )

    def mark_intent_executed(
        self, idempotency_key: str, *, canvas_id: str | None = None, external_id: str
    ) -> SideEffectIntent:
        canvas_id = canvas_id or self._legacy_canvas(idempotency_key)
        self._scoped_intent(idempotency_key, canvas_id)
        return intents.mark_executed(
            self.conn, clock=self.clock, tenant_id=self.tenant_id, canvas_id=canvas_id,
            idempotency_key=idempotency_key, external_id=external_id,
        )

    def mark_intent_ambiguous(
        self, idempotency_key: str, *, canvas_id: str | None = None, error: str, external_id: str | None
    ) -> SideEffectIntent:
        canvas_id = canvas_id or self._legacy_canvas(idempotency_key)
        self._scoped_intent(idempotency_key, canvas_id)
        return intents.mark_ambiguous(
            self.conn, clock=self.clock, tenant_id=self.tenant_id, canvas_id=canvas_id,
            idempotency_key=idempotency_key, error=error, external_id=external_id,
        )

    def mark_intent_reconciled(
        self, idempotency_key: str, *, canvas_id: str | None = None, external_id: str | None = None
    ) -> SideEffectIntent:
        canvas_id = canvas_id or self._legacy_canvas(idempotency_key)
        self._scoped_intent(idempotency_key, canvas_id)
        return intents.mark_reconciled(
            self.conn, clock=self.clock, tenant_id=self.tenant_id, canvas_id=canvas_id,
            idempotency_key=idempotency_key, external_id=external_id,
        )

    def mark_intent_failed(
        self, idempotency_key: str, *, canvas_id: str | None = None, error: str, next_retry_at: float | None = None
    ) -> SideEffectIntent:
        canvas_id = canvas_id or self._legacy_canvas(idempotency_key)
        self._scoped_intent(idempotency_key, canvas_id)
        return intents.mark_failed(
            self.conn, clock=self.clock, tenant_id=self.tenant_id, canvas_id=canvas_id,
            idempotency_key=idempotency_key, error=error, next_retry_at=next_retry_at,
        )

    def reset_failed_model_intent(
        self, idempotency_key: str, *, canvas_id: str
    ) -> SideEffectIntent:
        """Return a strictly eligible terminal model failure to pending."""
        self._require_canvas_scope(canvas_id)
        return intents.reset_failed_model_intent(
            self.conn, clock=self.clock, tenant_id=self.tenant_id, canvas_id=canvas_id,
            idempotency_key=idempotency_key,
        )

    def get_intent(
        self, idempotency_key: str, *, canvas_id: str | None = None
    ) -> SideEffectIntent | None:
        """Load an explicit scope; retain default-store legacy lookup only."""
        if canvas_id is not None:
            return self._scoped_intent(idempotency_key, canvas_id)
        rows = self.conn.execute(
            "SELECT * FROM side_effect_intents WHERE tenant_id=? AND idempotency_key=?",
            (self.tenant_id, idempotency_key),
        ).fetchall()
        if len(rows) != 1:
            return None
        canvas_id = str(rows[0]["canvas_id"])
        self._require_canvas_scope(canvas_id)
        return intents.get_intent(
            self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id,
            idempotency_key=idempotency_key,
        )

    def list_incomplete_intents(self, canvas_id: str | None = None) -> list[SideEffectIntent]:
        if canvas_id is not None:
            self._require_canvas_scope(canvas_id)
            return intents.list_incomplete(self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id)
        allowed = self._allowed_canvas_ids()
        if allowed is None:
            return intents.list_incomplete(self.conn, tenant_id=self.tenant_id)
        return [record for scope in allowed for record in intents.list_incomplete(
            self.conn, tenant_id=self.tenant_id, canvas_id=scope
        )]


__all__ = ["IntentStoreMixin"]
