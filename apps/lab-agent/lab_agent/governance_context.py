"""Construction of per-run governance state."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from lab_agent.adapters.base import AdapterResponse, Message, ModelAdapter, ToolSpec
from lab_agent.config import Settings
from lab_agent.model_request import estimate_usage
from lab_agent.models.governance import (
    DataClassification,
    RoutingDecision,
    TaskContext,
    TaskStage,
    Usage,
)
from lab_agent.policy import (
    GovernanceBudget,
    LocalityPolicy,
    RoutingTable,
    build_locality_policy,
    build_routing_table,
)
from lab_agent.state_store import StateStore


@dataclass
class GovernanceContext:
    """Policy and accounting state shared by governed calls in one run."""

    store: StateStore
    settings: Settings
    canvas_id: str
    adapters: dict[str, ModelAdapter]
    routing: RoutingTable
    locality: LocalityPolicy
    budget: GovernanceBudget
    run_id: str = "unscoped"
    round_index: int = 0
    routed_models: dict[TaskStage, RoutingDecision] = field(default_factory=dict)
    classifications: list[DataClassification] = field(
        default_factory=lambda: [DataClassification.UNKNOWN]
    )

    def start_run(self, run_id: str, sources: list[dict[str, object]]) -> None:
        """Reset per-trigger accounting and classifications for a new workflow run."""
        self.run_id = run_id
        self.round_index = 0
        self.routed_models.clear()
        self.budget = GovernanceBudget.for_canvas(
            self.store, self.settings, self.canvas_id, run_id=run_id
        )
        self.set_source_metadata(sources)

    def set_source_metadata(self, sources: list[dict[str, object]]) -> None:
        """Replace classifications at a new trigger boundary; missing stays unknown."""
        self.classifications = list(TaskContext.from_source_metadata(sources).classifications)

    def estimate_usage(
        self,
        provider: str,
        model: str,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        response_schema: dict | None,
        schema_name: str,
        response: AdapterResponse | None = None,
        request_id: str | None = None,
    ) -> Usage:
        return estimate_usage(
            provider, model, messages, response, request_id, tools=tools,
            response_schema=response_schema, schema_name=schema_name,
            max_completion_tokens=self.settings.model_max_output_tokens,
        )

    def merge_message_metadata(self, messages: list[Message]) -> None:
        """Add classifications declared on retrieved tool-result envelopes."""
        sources: list[dict[str, object]] = []
        for message in messages:
            if message.get("role") != "tool":
                continue
            try:
                payload = json.loads(str(message.get("content") or ""))
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(payload, dict) and (
                payload.get("untrusted_data") is True
                or payload.get("operational_status") is True
                or (payload.get("error") == "tool_result_unavailable")
            ):
                sources.append({"data_classification": payload.get("data_classification")})
        if not sources:
            return
        evidence = TaskContext.from_source_metadata(sources).classifications
        self.classifications = list(dict.fromkeys([*self.classifications, *evidence]))


def build_context(
    store: StateStore,
    settings: Settings,
    canvas_id: str,
    adapters: dict[str, ModelAdapter],
    source_metadata: list[dict[str, object]] | None = None,
) -> GovernanceContext:
    """Build fail-closed policy state from settings and source metadata."""
    task = TaskContext.from_source_metadata(source_metadata or [])
    return GovernanceContext(
        store=store,
        settings=settings,
        canvas_id=canvas_id,
        adapters=adapters,
        routing=build_routing_table(settings),
        locality=build_locality_policy(settings),
        budget=GovernanceBudget.for_canvas(store, settings, canvas_id, run_id="unscoped"),
        classifications=list(task.classifications),
    )


__all__ = ["GovernanceContext", "build_context"]
