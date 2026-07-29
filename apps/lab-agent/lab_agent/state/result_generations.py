"""Tenant-bound facade for durable ExperimentResult generation records."""

from __future__ import annotations

import sqlite3

from lab_agent.models.experiment import ExperimentResult
from lab_agent.state import result_generation_repository as repository
from lab_agent.state.models import Clock
from lab_agent.state.result_generation_repository import (
    ResultGeneration,
    ResultGenerationConflictError,
)


class ResultGenerationStoreMixin:
    """Inject the bound tenant and validate canvas scope at the store boundary."""

    conn: sqlite3.Connection
    clock: Clock
    tenant_id: str

    def _require_canvas_scope(self, canvas_id: str) -> None:
        raise NotImplementedError

    def get_result_generation(
        self,
        canvas_id: str,
        setup_id: str,
        round_index: int,
        proposal_hash: str,
        validation_result_hash: str,
    ) -> ResultGeneration | None:
        self._require_canvas_scope(canvas_id)
        return repository.get(
            self.conn,
            tenant_id=self.tenant_id,
            canvas_id=canvas_id,
            setup_id=setup_id,
            round_index=round_index,
            proposal_hash=proposal_hash,
            validation_result_hash=validation_result_hash,
        )

    def persist_result_generation(
        self,
        canvas_id: str,
        setup_id: str,
        round_index: int,
        proposal_hash: str,
        validation_result_hash: str,
        result: ExperimentResult,
    ) -> ResultGeneration:
        self._require_canvas_scope(canvas_id)
        return repository.persist(
            self.conn,
            clock=self.clock,
            tenant_id=self.tenant_id,
            canvas_id=canvas_id,
            setup_id=setup_id,
            round_index=round_index,
            proposal_hash=proposal_hash,
            validation_result_hash=validation_result_hash,
            result=result,
        )


__all__ = ["ResultGeneration", "ResultGenerationConflictError", "ResultGenerationStoreMixin"]
