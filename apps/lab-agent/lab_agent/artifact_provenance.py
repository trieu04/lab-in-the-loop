"""Artifact provenance derived from routed model calls or harness decisions."""

from __future__ import annotations

from lab_agent.adapters.base import ModelAdapter
from lab_agent.config import Settings
from lab_agent.governance_context import GovernanceContext
from lab_agent.models.artifact import ArtifactProvenance
from lab_agent.models.governance import TaskStage


def model_provenance(
    adapter: ModelAdapter,
    settings: Settings,
    stage: TaskStage,
    *,
    source_widget_id: str,
    trigger_id: str,
) -> ArtifactProvenance:
    """Use the actual governed route; retain configured provenance for legacy adapters."""
    context = getattr(adapter, "context", None)
    if isinstance(context, GovernanceContext):
        decision = context.routed_models.get(stage)
        if decision is None:
            raise RuntimeError(f"no successful routed model call recorded for {stage.value!r}")
        provider, model = decision.provider, decision.model
    else:
        provider = settings.model_provider
        model = settings.anthropic_model if provider == "claude" else settings.openai_model
    return ArtifactProvenance(
        provider=provider,
        model_name=model,
        trigger_id=trigger_id,
        source_widget_id=source_widget_id,
    )


def harness_provenance(*, source_widget_id: str, trigger_id: str) -> ArtifactProvenance:
    """Mark a synthetic closure as harness-produced, never model-produced."""
    return ArtifactProvenance(
        provider="harness",
        model_name="governance",
        trigger_id=trigger_id,
        source_widget_id=source_widget_id,
    )


__all__ = ["harness_provenance", "model_provenance"]
