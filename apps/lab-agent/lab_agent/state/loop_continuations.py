"""Tenant-bound durable state for staged experiment-loop successors."""

from __future__ import annotations

import sqlite3

from lab_agent.state import loop_continuation_repository as repository
from lab_agent.state.loop_continuation_models import (
    LoopContinuation,
    LoopContinuationLineageError,
)
from lab_agent.state.models import Clock


class LoopContinuationStoreMixin:
    """Inject the bound tenant and validate canvas scope at the store boundary."""

    conn: sqlite3.Connection
    clock: Clock
    tenant_id: str

    def _require_canvas_scope(self, canvas_id: str) -> None:
        raise NotImplementedError

    def ensure_loop_continuation(
        self,
        canvas_id: str,
        loop_scope: str,
        round_index: int,
        setup_id: str,
        result_id: str,
    ) -> LoopContinuation:
        self._require_canvas_scope(canvas_id)
        return repository.ensure(
            self.conn,
            clock=self.clock,
            tenant_id=self.tenant_id,
            canvas_id=canvas_id,
            loop_scope=loop_scope,
            round_index=round_index,
            setup_id=setup_id,
            result_id=result_id,
        )

    def get_loop_continuation(
        self,
        canvas_id: str,
        loop_scope: str,
    ) -> LoopContinuation | None:
        self._require_canvas_scope(canvas_id)
        return repository.get(
            self.conn,
            tenant_id=self.tenant_id,
            canvas_id=canvas_id,
            loop_scope=loop_scope,
        )

    def resolve_loop_continuation(
        self,
        canvas_id: str,
        setup_id: str,
        result_id: str,
        round_index: int,
    ) -> LoopContinuation:
        self._require_canvas_scope(canvas_id)
        return repository.resolve(
            self.conn,
            clock=self.clock,
            tenant_id=self.tenant_id,
            canvas_id=canvas_id,
            setup_id=setup_id,
            result_id=result_id,
            round_index=round_index,
        )

    def save_loop_continuation(self, value: LoopContinuation) -> LoopContinuation:
        self._require_canvas_scope(value.canvas_id)
        if value.tenant_id != self.tenant_id:
            raise ValueError("loop continuation tenant does not match this store")
        return repository.save(self.conn, clock=self.clock, value=value)

    def list_staged_loop_continuations(self, canvas_id: str) -> list[LoopContinuation]:
        self._require_canvas_scope(canvas_id)
        return repository.list_staged(
            self.conn,
            tenant_id=self.tenant_id,
            canvas_id=canvas_id,
        )

    def record_loop_result(
        self,
        canvas_id: str,
        setup_id: str,
        round_index: int,
        result_id: str,
        proposal_hash: str,
    ) -> LoopContinuation | None:
        self._require_canvas_scope(canvas_id)
        return repository.record_result(
            self.conn,
            clock=self.clock,
            tenant_id=self.tenant_id,
            canvas_id=canvas_id,
            setup_id=setup_id,
            round_index=round_index,
            result_id=result_id,
            proposal_hash=proposal_hash,
        )


__all__ = ["LoopContinuation", "LoopContinuationLineageError", "LoopContinuationStoreMixin"]
