"""Contract tests for deterministic and disabled in-silico adapters."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lab_agent.integrations.in_silico import (
    DeterministicInSilicoAdapter,
    DisabledRealInSilicoAdapter,
    InSilicoAdapter,
    InSilicoNotReadyError,
    InSilicoProviderError,
    InSilicoSchemaError,
    InSilicoTimeoutError,
    MockFailureMode,
    RealAdapterReadiness,
)
from lab_agent.models.experiment import ExperimentSetup
from lab_agent.models.validation import (
    InSilicoRequest,
    ValidationDecision,
    ValidationMode,
    hash_proposal,
)

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def _request(**setup_updates: object) -> InSilicoRequest:
    setup = ExperimentSetup(
        rationale="Validate a bounded proposal.",
        conditions=["temperature=25C"],
        steps=["measure baseline"],
        expected_readouts=["signal"],
        hypothesis="The signal remains measurable.",
        success_criteria=["signal is recorded"],
    ).model_copy(update=setup_updates)
    return InSilicoRequest(
        request_id="request-1",
        setup_id="setup-1",
        setup=setup,
        proposal_hash=hash_proposal(setup),
        requested_at=NOW,
    )


async def test_deterministic_adapter_proceeds_for_complete_structure() -> None:
    adapter = DeterministicInSilicoAdapter()

    first = await adapter.validate(_request())
    second = await adapter.validate(_request())

    assert isinstance(adapter, InSilicoAdapter)
    assert first == second
    assert first.decision is ValidationDecision.PROCEED
    assert first.mode is ValidationMode.DRY_RUN
    assert first.risk_flags == ("dry-run-only",)
    assert "No scientific simulation" in first.uncertainty


@pytest.mark.parametrize(
    ("updates", "decision", "risk"),
    [
        ({"success_criteria": []}, ValidationDecision.REVISE, "incomplete-review-contract"),
        ({"steps": []}, ValidationDecision.REJECT, "structurally-incomplete-proposal"),
    ],
)
async def test_deterministic_adapter_returns_bounded_non_authorizing_decisions(
    updates: dict[str, object], decision: ValidationDecision, risk: str
) -> None:
    result = await DeterministicInSilicoAdapter().validate(_request(**updates))

    assert result.decision is decision
    assert risk in result.risk_flags
    assert result.adapter_name == "deterministic-mock"


@pytest.mark.parametrize(
    ("failure_mode", "error_type"),
    [
        (MockFailureMode.TIMEOUT, InSilicoTimeoutError),
        (MockFailureMode.PROVIDER_FAILURE, InSilicoProviderError),
        (MockFailureMode.INVALID_SCHEMA, InSilicoSchemaError),
    ],
)
async def test_deterministic_adapter_fails_closed_with_typed_errors(
    failure_mode: MockFailureMode, error_type: type[Exception]
) -> None:
    adapter = DeterministicInSilicoAdapter(failure_mode=failure_mode)

    with pytest.raises(error_type, match=failure_mode.value):
        await adapter.validate(_request())


async def test_real_adapter_stays_disabled_when_every_readiness_flag_is_true() -> None:
    adapter = DisabledRealInSilicoAdapter(
        RealAdapterReadiness(
            endpoint="https://validator.example",
            security_approved=True,
            sla_approved=True,
            locality_authorized=True,
            identity_provider_ready=True,
        )
    )

    assert adapter.readiness.ready
    with pytest.raises(InSilicoNotReadyError, match="implementation is not installed"):
        await adapter.validate(_request())


async def test_real_adapter_reports_all_missing_readiness_gates_without_dispatch() -> None:
    adapter = DisabledRealInSilicoAdapter(RealAdapterReadiness(endpoint="http://unsafe.example"))

    assert adapter.readiness.blockers == (
        "https_endpoint",
        "security_approval",
        "sla_approval",
        "locality_authorization",
        "identity_provider",
    )
    with pytest.raises(InSilicoNotReadyError, match="https_endpoint"):
        await adapter.validate(_request())
