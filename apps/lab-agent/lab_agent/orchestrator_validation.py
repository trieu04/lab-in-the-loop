"""Typed, fail-closed in-silico validation runtime."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import ValidationError

from lab_agent import artifact_compat, durable_browser, nodes
from lab_agent.approval import TransitionEvidence, require_transition
from lab_agent.artifact_store import ArtifactStore
from lab_agent.config import Settings
from lab_agent.integrations.in_silico import (
    InSilicoAdapter,
    InSilicoAdapterError,
    InSilicoSchemaError,
)
from lab_agent.mcp_client import MCPClient
from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
from lab_agent.models.experiment import ExperimentSetup
from lab_agent.models.states import DecisionState
from lab_agent.models.validation import (
    InSilicoRequest,
    InSilicoResult,
    ValidationDecision,
    ValidationMode,
    hash_proposal,
    hash_validation_result,
)
from lab_agent.state_store import StateStore

_DRY_RUN_LABEL = "DETERMINISTIC DRY RUN — NOT SCIENTIFIC VALIDATION"


class TypedSetupArtifactError(RuntimeError):
    pass


@dataclass(frozen=True)
class ValidationOutcome:
    setup_id: str
    proposal_hash: str
    result_hash: str
    validation_artifact_widget_id: str
    result: InSilicoResult
    projected_state: DecisionState


def load_typed_setup(store: StateStore, canvas_id: str, setup_id: str) -> ExperimentSetup:
    """Load a Setup Browser's canonical payload, never its rendered Canvas text."""

    document = ArtifactStore(store.conn).get_artifact_by_widget(
        canvas_id=canvas_id, widget_id=setup_id
    )
    if document is None or document.artifact_type is not ArtifactType.SETUP:
        raise TypedSetupArtifactError("canonical setup artifact is required for validation")
    try:
        return ExperimentSetup.model_validate(document.payload)
    except ValidationError as exc:
        raise TypedSetupArtifactError("canonical setup artifact payload is invalid") from exc


def _request_id(setup_id: str, proposal_hash: str) -> str:
    material = f"{setup_id}:{proposal_hash}".encode()
    return f"in-silico-{hashlib.sha256(material).hexdigest()}"


def _target(result: InSilicoResult) -> DecisionState:
    targets = {
        ValidationDecision.PROCEED: DecisionState.NEEDS_SCIENTIST_REVIEW,
        ValidationDecision.REVISE: DecisionState.NEEDS_REVIEW,
        ValidationDecision.REJECT: DecisionState.REJECTED,
    }
    return targets[result.decision]


def _validate_result(value: object, proposal_hash: str) -> InSilicoResult:
    if not isinstance(value, InSilicoResult):
        raise InSilicoSchemaError("adapter returned a non-InSilicoResult value")
    if value.proposal_hash != proposal_hash:
        raise InSilicoSchemaError("adapter result proposal hash does not match request")
    return value


def _existing_result(
    store: StateStore,
    canvas_id: str,
    discriminator: str,
    proposal_hash: str,
) -> InSilicoResult | None:
    artifact_key = artifact_compat.artifact_key(canvas_id, ArtifactType.IN_SILICO, discriminator)
    document = artifact_compat.find_existing_artifact(
        ArtifactStore(store.conn),
        canvas_id=canvas_id,
        artifact_type=ArtifactType.IN_SILICO,
        keys=(artifact_key,),
    )
    if document is not None:
        try:
            return InSilicoResult.model_validate(document.payload["validation"])
        except (KeyError, ValidationError) as exc:
            raise TypedSetupArtifactError("canonical validation artifact payload is invalid") from exc
    durable = store.list_validation_results(canvas_id, proposal_hash=proposal_hash)
    if len(durable) > 1:
        raise TypedSetupArtifactError("multiple validation results exist for the current proposal")
    return durable[0] if durable else None


def _audit_failure(
    store: StateStore,
    canvas_id: str,
    setup_id: str,
    proposal_hash: str,
    error: Exception,
    round_index: int,
) -> None:
    store.append_audit_event(
        canvas_id,
        "in_silico_validation_failed",
        {
            "setup_id": setup_id[:200],
            "proposal_hash": proposal_hash,
            "error_type": type(error).__name__,
        },
        round=round_index,
    )


def _payload(
    result: InSilicoResult, setup_id: str, round_index: int, target: DecisionState
) -> tuple[str, dict[str, object]]:
    title = f"{nodes.EXP_VALIDATION} {result.decision.value}"
    result_hash = hash_validation_result(result)
    validation_label = (
        _DRY_RUN_LABEL
        if result.mode is ValidationMode.DRY_RUN
        else "EXTERNAL VALIDATION — REVIEW PROVIDER AND ALGORITHM METADATA"
    )
    rendered = "\n".join(
        (
            title,
            f"Setup: {setup_id}",
            f"Round: {round_index}",
            f"Decision: {result.decision.value}",
            f"Predicted outcome: {result.predicted_outcome}",
            validation_label,
        )
    )
    return title, {
        "title": title,
        "round": round_index,
        "setup_id": setup_id,
        "proposal_hash": result.proposal_hash,
        "result_hash": result_hash,
        "validation": result.model_dump(mode="json"),
        "approval_status": {"state": target.value, "authorized": False},
        "validation_label": validation_label,
        durable_browser.RENDERED_TEXT_KEY: rendered,
    }


async def run_in_silico_validation(
    mcp: MCPClient,
    settings: Settings,
    store: StateStore,
    adapter: InSilicoAdapter,
    canvas_id: str,
    setup_id: str,
    round_index: int,
) -> ValidationOutcome:
    """Validate a canonical setup, persist its result, then render one Browser view."""

    setup = load_typed_setup(store, canvas_id, setup_id)
    proposal_hash = hash_proposal(setup)
    request_id = _request_id(setup_id, proposal_hash)
    discriminator = f"in-silico/setup:{setup_id}/proposal:{proposal_hash}"
    require_transition(DecisionState.APPROVED_FOR_IN_SILICO, DecisionState.IN_SILICO_RUNNING)
    result = _existing_result(store, canvas_id, discriminator, proposal_hash)
    if result is None:
        request = InSilicoRequest(
            request_id=request_id,
            setup_id=setup_id,
            setup=setup,
            proposal_hash=proposal_hash,
            requested_at=datetime.now(UTC),
        )
        try:
            result = _validate_result(await adapter.validate(request), proposal_hash)
        except (InSilicoAdapterError, ValidationError) as exc:
            error = exc if isinstance(exc, InSilicoAdapterError) else InSilicoSchemaError(str(exc))
            _audit_failure(store, canvas_id, setup_id, proposal_hash, error, round_index)
            raise error
    result = store.append_validation_result(canvas_id, result)
    evidence = TransitionEvidence(proposal_hash=proposal_hash, validation_result=result)
    require_transition(DecisionState.IN_SILICO_RUNNING, DecisionState.IN_SILICO_COMPLETE, evidence)
    target = _target(result)
    require_transition(DecisionState.IN_SILICO_COMPLETE, target, evidence)
    title, payload = _payload(result, setup_id, round_index, target)
    widget_id = await durable_browser.write_artifact_browser_durable(
        mcp,
        store,
        settings,
        canvas_id=canvas_id,
        artifact_type=ArtifactType.IN_SILICO,
        state=target,
        title=title,
        payload=payload,
        provenance=ArtifactProvenance(
            provider="harness" if result.mode is ValidationMode.DRY_RUN else "in_silico",
            model_name=result.adapter_name,
            trigger_id=request_id,
            source_widget_id=setup_id,
        ),
        discriminator=discriminator,
        round_index=round_index,
        predecessor_id=setup_id,
        edge_kind="setup_validation",
    )
    return ValidationOutcome(
        setup_id, proposal_hash, hash_validation_result(result), widget_id, result, target
    )


__all__ = [
    "TypedSetupArtifactError",
    "ValidationOutcome",
    "load_typed_setup",
    "run_in_silico_validation",
]
