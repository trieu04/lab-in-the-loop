"""Provider-neutral governance vocabulary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class UsageStatus(StrEnum):
    """Provenance of a normalized usage or cost figure."""

    EXACT = "exact"
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"

class DataClassification(StrEnum):
    """Content sensitivity used by fail-closed locality authorization."""

    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"
    UNKNOWN = "unknown"

@dataclass(frozen=True)
class TaskContext:
    """Classifications derived from evidence/source metadata for one call."""

    classifications: tuple[DataClassification, ...]

    @classmethod
    def from_source_metadata(cls, sources: list[dict[str, object]]) -> TaskContext:
        classifications: list[DataClassification] = []
        for source in sources:
            raw = source.get("data_classification", source.get("classification"))
            try:
                classifications.append(DataClassification(str(raw)))
            except (TypeError, ValueError):
                classifications.append(DataClassification.UNKNOWN)
        return cls(tuple(classifications) or (DataClassification.UNKNOWN,))

class TaskStage(StrEnum):
    """Workflow stage served by a model call and used as the routing key."""

    SETUP = "setup"
    MOCK_RESULT = "mock_result"
    LOOP_DECISION = "loop_decision"
    IN_SILICO = "in_silico"
    ANALYSIS = "analysis"


class StopReason(StrEnum):
    """Distinct, auditable reasons an experiment run halts."""

    TOKEN_BUDGET = "token_budget"
    COST_BUDGET = "cost_budget"
    MAX_ROUNDS = "max_rounds"
    WALL_TIME = "wall_time"
    NO_PROGRESS = "no_progress"
    LOCALITY_DENIAL = "locality_denial"
    RESERVATION_DENIAL = "reservation_denial"
    MODEL_DECISION = "model_decision"


@dataclass(frozen=True)
class TerminalStopEvent:
    canvas_id: str
    trigger_id: str
    predecessor_id: str
    reason: StopReason
    round_index: int
    closure_id: str | None
    notification_eligible: bool = True

    @property
    def audit_payload(self) -> dict[str, object]:
        return {"canvas_id": self.canvas_id, "trigger_id": self.trigger_id, "predecessor_id": self.predecessor_id, "reason": self.reason.value, "round": self.round_index, "closure_id": self.closure_id, "notification_eligible": self.notification_eligible}


@dataclass(frozen=True)
class Usage:
    """Normalized token usage and request metadata for one provider turn."""

    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    request_id: str | None = None
    status: UsageStatus = UsageStatus.EXACT

    @classmethod
    def unavailable(cls, provider: str, model: str, request_id: str | None = None) -> Usage:
        """Record a turn whose provider returned no token counts."""
        return cls(
            provider=provider,
            model=model,
            request_id=request_id,
            status=UsageStatus.UNAVAILABLE,
        )

    @property
    def audit_metadata(self) -> dict[str, object]:
        """Return ids, counts, and status without prompt or response content."""
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "request_id": self.request_id,
            "status": self.status.value,
        }


@dataclass(frozen=True)
class CostEstimate:
    """A priced usage figure and the pricing version that produced it."""

    cost_usd: float
    pricing_version: str
    status: UsageStatus


@dataclass(frozen=True)
class RoutingDecision:
    """The provider/model selected for a stage and its audit-safe reason."""

    stage: TaskStage
    provider: str
    model: str
    reason: str
    fallback: bool = False

    @property
    def audit_metadata(self) -> dict[str, object]:
        return {
            "stage": self.stage.value,
            "provider": self.provider,
            "model": self.model,
            "reason": self.reason,
            "fallback": self.fallback,
        }


@dataclass(frozen=True)
class Authorization:
    """Outcome of a locality check; ambiguity always yields ``allowed=False``."""

    allowed: bool
    reason: str


@dataclass
class Budget:
    """Resource envelope with committed and in-flight reservation totals."""

    token_limit: int | None = None
    cost_limit_usd: float | None = None
    tokens_committed: int = 0
    cost_committed_usd: float = 0.0
    tokens_reserved: int = 0
    cost_reserved_usd: float = 0.0

    def would_exceed(self, tokens: int, cost_usd: float) -> bool:
        """Return whether adding usage would breach either configured limit."""
        if self.token_limit is not None and (
            self.tokens_committed + self.tokens_reserved + tokens > self.token_limit
        ):
            return True
        if self.cost_limit_usd is not None and (
            self.cost_committed_usd + self.cost_reserved_usd + cost_usd > self.cost_limit_usd
        ):
            return True
        return False

    @property
    def exhausted_reason(self) -> StopReason | None:
        """Return the first committed limit already met."""
        if self.token_limit is not None and self.tokens_committed >= self.token_limit:
            return StopReason.TOKEN_BUDGET
        if self.cost_limit_usd is not None and self.cost_committed_usd >= self.cost_limit_usd:
            return StopReason.COST_BUDGET
        return None


__all__ = [
    "Authorization",
    "Budget",
    "CostEstimate",
    "DataClassification",
    "RoutingDecision",
    "StopReason",
    "TaskContext",
    "TerminalStopEvent",
    "TaskStage",
    "Usage",
    "UsageStatus",
]
