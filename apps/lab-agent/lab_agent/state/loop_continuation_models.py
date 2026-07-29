"""Typed continuation identity and validation contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class LoopContinuationLineageError(RuntimeError):
    """Continuation lineage is ambiguous or incompatible with this branch."""


@dataclass(frozen=True)
class LoopContinuation:
    tenant_id: str
    canvas_id: str
    loop_scope: str
    run_id: str
    started_at: float
    round_index: int
    observed_round: int
    previous_result_signature: str
    no_progress_streak: int
    predecessor_setup_id: str
    predecessor_result_id: str
    staged_setup_id: str = ""
    staged_result_id: str = ""
    staged_proposal_hash: str = ""
    branch_setup_id: str = ""
    updated_at: float = 0.0

    def __post_init__(self) -> None:
        limits = {
            "tenant_id": 128,
            "canvas_id": 200,
            "loop_scope": 200,
            "run_id": 200,
            "predecessor_setup_id": 200,
            "predecessor_result_id": 200,
        }
        for name, maximum in limits.items():
            value = getattr(self, name)
            if not isinstance(value, str) or not 1 <= len(value) <= maximum:
                raise ValueError(f"{name} is invalid")
        for name in ("staged_setup_id", "staged_result_id", "branch_setup_id"):
            if not isinstance(getattr(self, name), str) or len(getattr(self, name)) > 200:
                raise ValueError(f"{name} is invalid")
        if self.round_index < 1 or not 0 <= self.observed_round <= self.round_index:
            raise ValueError("loop continuation rounds are invalid")
        if self.no_progress_streak < 0 or len(self.previous_result_signature) > 64:
            raise ValueError("loop continuation state is invalid")
        if self.staged_proposal_hash and not SHA256_HEX.fullmatch(self.staged_proposal_hash):
            raise ValueError("staged_proposal_hash must be a SHA-256 hash")


def run_id(tenant_id: str, canvas_id: str, loop_scope: str) -> str:
    """Derive a collision-resistant stable continuation identity."""
    identity = json.dumps([tenant_id, canvas_id, loop_scope], separators=(",", ":"))
    return f"loop-run:{hashlib.sha256(identity.encode()).hexdigest()}"


__all__ = ["LoopContinuation", "LoopContinuationLineageError", "SHA256_HEX", "run_id"]
