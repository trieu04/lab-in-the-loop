"""Governed model calls: locality, routing, budget, and durable intent."""
from __future__ import annotations

from lab_agent.adapters.base import (
    AdapterResponse,
    DeterministicProviderError,
    Message,
    ProviderCallError,
    ToolSpec,
    TransientProviderError,
)
from lab_agent.config import Settings
from lab_agent.governance_context import GovernanceContext, build_context
from lab_agent.model_request import SubmittedCallReconciler, request_digest
from lab_agent.models.governance import StopReason, TaskStage, UsageStatus
from lab_agent.policy import estimate_cost
from lab_agent.policy.budget import BudgetExceededError, PostResponseBudgetExceededError
from lab_agent.recovery import idempotency_key
from lab_agent.state_store import IntentStatus

_STAGE_BY_SCHEMA = {
    "ExperimentSetup": TaskStage.SETUP,
    "ExperimentResult": TaskStage.MOCK_RESULT,
    "LoopDecision": TaskStage.LOOP_DECISION,
}
class LocalityDeniedError(RuntimeError):
    """No constructed provider may receive the classifications for a call."""
    reason = StopReason.LOCALITY_DENIAL
class ModelCallExhaustedError(RuntimeError):
    """A logical model call reached its durable attempt bound."""
class ModelCallInFlightError(RuntimeError):
    """A durable submitted/executed call cannot be safely redispatched."""
def _backoff_at(settings: Settings, now: float, attempt: int) -> float:
    delay = min(settings.retry_base_seconds * (2**attempt), settings.retry_max_seconds)
    return now + delay

async def governed_generate(
    ctx: GovernanceContext,
    *,
    stage: TaskStage,
    messages: list[Message],
    tools: list[ToolSpec] | None = None,
    response_schema: dict | None = None,
    schema_name: str = "result",
) -> AdapterResponse:
    preferences = [provider for provider in ctx.routing.preferences(stage) if provider in ctx.adapters]
    allowed = ctx.locality.allowed_providers(preferences, ctx.classifications)
    if not allowed:
        ctx.store.append_audit_event(
            ctx.canvas_id, "locality_denied",
            {"stage": stage.value, "classifications": [c.value for c in ctx.classifications]},
            round=ctx.round_index,
        )
        raise LocalityDeniedError(f"no authorized provider for stage {stage.value!r}")
    decision = ctx.routing.select(stage, allowed)
    adapter = ctx.adapters[decision.provider]
    digest = request_digest(messages, tools, response_schema, schema_name)
    call_scope = f"model_call:{ctx.run_id}:{stage.value}:round:{ctx.round_index}"
    key = idempotency_key(ctx.canvas_id, call_scope, digest)
    intent = ctx.store.prepare_intent(
        idempotency_key=key, canvas_id=ctx.canvas_id, kind="model_call", input_hash=digest
    )
    if intent.status is IntentStatus.RECONCILED:
        raise RuntimeError(f"model call for {stage.value!r} is already reconciled")
    if intent.status in {IntentStatus.SUBMITTED, IntentStatus.EXECUTED}:
        if isinstance(adapter, SubmittedCallReconciler):
            recovered = await adapter.reconcile_submission(
                idempotency_key=key, request_id=intent.external_id
            )
            if recovered is not None:
                estimate = ctx.estimate_usage(
                    decision.provider, decision.model, messages, tools, response_schema, schema_name
                )
                usage = recovered.usage
                if usage is None or usage.status is UsageStatus.UNAVAILABLE:
                    usage = estimate
                cost = estimate_cost(usage, ctx.settings.model_pricing, ctx.settings.pricing_version)
                reservation = ctx.budget.reserve(
                    estimate.total_tokens, estimate_cost(estimate, ctx.settings.model_pricing, ctx.settings.pricing_version),
                    reservation_id=f"{key}:{intent.attempt_count}", intent_key=f"{key}:{intent.attempt_count}",
                )
                external_id = usage.request_id or intent.external_id or ""
                if intent.status is IntentStatus.SUBMITTED:
                    ctx.store.mark_intent_executed(key, external_id=external_id)
                ctx.budget.commit(reservation, usage, cost)
                ctx.store.mark_intent_reconciled(key, external_id=external_id)
                ctx.budget.raise_if_exhausted()
                return recovered
        ctx.store.append_audit_event(
            ctx.canvas_id, "model_call_reconciliation_blocked",
            {"key": key[:16], "stage": stage.value, "capability": "unsupported"}, round=ctx.round_index,
        )
        raise ModelCallInFlightError(
            f"model call for {stage.value!r} is durably submitted; reconciliation unsupported"
        )
    if intent.status is IntentStatus.FAILED and intent.next_retry_at is None:
        if (intent.last_error or "").startswith("ambiguous:"):
            ctx.store.append_audit_event(
                ctx.canvas_id, "model_call_reconciliation_blocked",
                {"key": key[:16], "stage": stage.value, "capability": "unsupported"}, round=ctx.round_index,
            )
            raise ModelCallInFlightError(
                f"model call for {stage.value!r} is ambiguous; reconciliation unsupported"
            )
        raise RuntimeError(f"model call for {stage.value!r} is non-retryable")
    if intent.next_retry_at is not None and intent.next_retry_at > ctx.store.clock():
        raise RuntimeError(f"model call for {stage.value!r} is not due for retry")
    if intent.attempt_count >= ctx.settings.model_call_max_attempts:
        ctx.store.append_audit_event(
            ctx.canvas_id, "model_call_exhausted",
            {"key": key[:16], "stage": stage.value, "attempts": intent.attempt_count},
            round=ctx.round_index,
        )
        raise ModelCallExhaustedError(
            f"model call for {stage.value!r} exhausted its attempt bound"
        )
    estimated_usage = ctx.estimate_usage(
        decision.provider, decision.model, messages, tools, response_schema, schema_name
    )
    estimated_tokens = estimated_usage.total_tokens
    estimated_cost = estimate_cost(
        estimated_usage, ctx.settings.model_pricing, ctx.settings.pricing_version
    )
    reservation = ctx.budget.reserve(
        estimated_tokens, estimated_cost,
        reservation_id=f"{key}:{intent.attempt_count + 1}", intent_key=f"{key}:{intent.attempt_count + 1}",
    )
    ctx.store.mark_intent_submitted(key)
    try:
        response = await adapter.generate(
            messages, tools=tools, response_schema=response_schema, schema_name=schema_name
        )
    except (BudgetExceededError, LocalityDeniedError):
        raise
    except DeterministicProviderError:
        ctx.budget.release(reservation)
        ctx.store.mark_intent_failed(key, error="provider_error")
        raise
    except TransientProviderError:
        ctx.budget.release(reservation)
        ctx.store.mark_intent_failed(
            key, error="provider_error",
            next_retry_at=_backoff_at(ctx.settings, ctx.store.clock(), intent.attempt_count + 1),
        )
        raise
    except ProviderCallError as exc:
        ctx.store.mark_intent_ambiguous(key, error="ambiguous:provider_error", external_id=exc.request_id)
        ctx.store.append_audit_event(
            ctx.canvas_id, "model_call_ambiguous", {"key": key[:16], "stage": stage.value},
            round=ctx.round_index,
        )
        raise
    except Exception:  # noqa: BLE001 - untyped provider errors are ambiguous
        ctx.store.mark_intent_ambiguous(key, error="ambiguous:provider_error", external_id=None)
        raise
    ctx.routed_models[stage] = decision
    usage = response.usage or estimated_usage
    if usage.status is UsageStatus.UNAVAILABLE:
        usage = ctx.estimate_usage(
            decision.provider, decision.model, messages, tools, response_schema, schema_name,
            response, usage.request_id,
        )
    cost = estimate_cost(usage, ctx.settings.model_pricing, ctx.settings.pricing_version)
    external_id = usage.request_id or ""
    ctx.store.mark_intent_executed(key, external_id=external_id)
    ctx.budget.commit(reservation, usage, cost)
    ctx.store.mark_intent_reconciled(key, external_id=external_id)
    ctx.store.append_audit_event(
        ctx.canvas_id, "model_call",
        {**decision.audit_metadata, **usage.audit_metadata,
         "cost_usd": cost.cost_usd, "cost_status": cost.status.value},
        round=ctx.round_index,
    )
    ctx.budget.raise_if_exhausted()
    return response
class GovernedAdapter:
    """Policy-routing wrapper that satisfies ``ModelAdapter``."""
    def __init__(self, context: GovernanceContext) -> None:
        self._context = context
    @property
    def context(self) -> GovernanceContext:
        return self._context
    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        response_schema: dict | None = None,
        schema_name: str = "result",
    ) -> AdapterResponse:
        self._context.merge_message_metadata(messages)
        return await governed_generate(
            self._context, stage=_STAGE_BY_SCHEMA.get(schema_name, TaskStage.SETUP),
            messages=messages, tools=tools, response_schema=response_schema,
            schema_name=schema_name,
        )
__all__ = [
    "GovernanceContext", "GovernedAdapter", "LocalityDeniedError", "ModelCallExhaustedError",
    "ModelCallInFlightError", "PostResponseBudgetExceededError", "build_context", "governed_generate",
]
