"""Regressions for actual-usage budget overshoot handling."""

from __future__ import annotations

import pytest

from lab_agent.adapters.base import AdapterResponse, Usage
from lab_agent.config import Settings
from lab_agent.model_gateway import (
    GovernedAdapter,
    PostResponseBudgetExceededError,
    build_context,
    governed_generate,
)
from lab_agent.models.governance import StopReason, TaskStage
from lab_agent.watch import process_once
from tests.fakes import FakeMCP, ScriptedAdapter, grounded_setup
from tests.test_model_gateway import MESSAGES, PRICING, SpyAdapter


@pytest.mark.parametrize(
    ("limits", "expected"),
    [
        ({"run_token_budget": 1_000}, StopReason.TOKEN_BUDGET),
        ({"run_cost_budget_usd": 1.0}, StopReason.COST_BUDGET),
    ],
)
async def test_actual_usage_overshoot_is_raised_before_response_returns(
    store, limits: dict[str, object], expected: StopReason
) -> None:
    usage = Usage(
        "openai",
        "gpt-4o-mini",
        prompt_tokens=1_000,
        completion_tokens=1_000,
        total_tokens=2_000,
    )
    adapter = SpyAdapter([AdapterResponse(parsed={"ok": True}, usage=usage)])
    settings = Settings(
        openai_api_key="key",
        model_pricing=PRICING,
        pricing_version="v1",
        provider_endpoints={"openai": "https://openai.example"},
        model_max_output_tokens=1,
        **limits,
    )
    context = build_context(
        store,
        settings,
        "canvas",
        {"openai": adapter},
        [{"classification": "internal"}],
    )

    with pytest.raises(PostResponseBudgetExceededError) as raised:
        await governed_generate(context, stage=TaskStage.SETUP, messages=MESSAGES)

    assert raised.value.reason is expected
    assert adapter.calls == 1
    assert context.budget.run.tokens_committed == 2_000
    assert store.list_incomplete_intents("canvas") == []


class OvershootAdapter(ScriptedAdapter):
    async def generate(self, *args, **kwargs):
        response = await super().generate(*args, **kwargs)
        return AdapterResponse(
            text=response.text,
            parsed=response.parsed,
            tool_calls=response.tool_calls,
            usage=Usage("openai", "gpt-4o-mini", total_tokens=2_000),
        )


def _workflow_settings() -> Settings:
    return Settings(
        artifact_public_base_url="https://lab.test",
        run_token_budget=1_000,
        model_pricing=PRICING,
        pricing_version="v1",
        model_max_output_tokens=1,
        provider_endpoints={"openai": "https://openai.example"},
    )


def _governed(store, settings: Settings, adapter: ScriptedAdapter) -> GovernedAdapter:
    context = build_context(
        store,
        settings,
        "canvas",
        {"openai": adapter},
        [{"classification": "internal"}],
    )
    return GovernedAdapter(context)


@pytest.mark.parametrize(
    ("workflow", "seed", "structured", "predecessor", "expected_schema_calls"),
    [
        (
            {
                "ideas_needing_setup": [
                    {
                        "widget_id": "idea1",
                        "ragcluster_id": "rag1",
                        "data_classification": "internal",
                    }
                ]
            },
            {"idea1": "{idea: A+B}"},
            {"ExperimentSetup": grounded_setup()},
            "idea1",
            [],
        ),
        (
            {
                "setups_needing_run": [
                    {
                        "widget_id": "setup1",
                        "robot_id": "robot1",
                        "title": "[EXP:Setup v001]",
                        "data_classification": "internal",
                        "execution_mode": "auto",
                    }
                ]
            },
            {"setup1": "Round: 1\nMix A+B"},
            {"ExperimentResult": {"summary": "mock result", "metrics": ["x=1"]}},
            "setup1",
            ["ExperimentResult"],
        ),
    ],
)
async def test_preloop_overshoot_writes_only_deduplicated_governance_closure(
    store,
    workflow: dict[str, object],
    seed: dict[str, str],
    structured: dict[str, object],
    predecessor: str,
    expected_schema_calls: list[str],
) -> None:
    """Budget overshoot must write exactly one deduplicated closure with authorized setup.

    For robot-execution path: use authorized auto-mode fixture (canonical setup,
    current validation result, ordered approvals, wet-lab enabled) to test that
    budget governance is durable and writes no duplicate closure.
    """
    from datetime import UTC, datetime

    from pydantic import SecretStr

    from lab_agent.approval_service import ApprovalSubmission, submit_approval
    from lab_agent.artifact_store import ArtifactStore
    from lab_agent.integrations.identity import (
        DevelopmentIdentity,
        StaticDevelopmentIdentityProvider,
        hash_development_credential,
    )
    from lab_agent.integrations.in_silico import DeterministicInSilicoAdapter
    from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
    from lab_agent.models.experiment import ExperimentSetup
    from lab_agent.models.states import DecisionState
    from lab_agent.models.validation import (
        ApprovalDecision,
        ApprovalRole,
        hash_validation_result,
    )
    from lab_agent.orchestrator_validation import run_in_silico_validation

    now = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)

    # For setups_needing_run path: set up authorized fixture
    if "setups_needing_run" in workflow:
        def _setup() -> ExperimentSetup:
            return ExperimentSetup(
                rationale="Grounded proposal.",
                conditions=["temperature=25C"],
                steps=["measure baseline"],
                expected_readouts=["signal"],
                hypothesis="Signal remains measurable.",
                success_criteria=["record signal"],
            )

        # Persist canonical setup
        document = ArtifactStore(store.conn).create_artifact(
            canvas_id="canvas",
            idempotency_key="setup",
            artifact_type=ArtifactType.SETUP,
            state=DecisionState.APPROVED_FOR_IN_SILICO,
            payload=_setup().model_dump(),
            provenance=ArtifactProvenance(provider="harness", source_widget_id="idea"),
            round=1,
        )
        ArtifactStore(store.conn).map_widget(document.opaque_id, canvas_id="canvas", widget_id="setup1")

        # Run validation to get deterministic result
        dummy_mcp = FakeMCP()
        await run_in_silico_validation(
            dummy_mcp,
            _workflow_settings(),
            store,
            DeterministicInSilicoAdapter(),
            "canvas",
            "setup1",
            1,
        )

        # Obtain ordered approvals
        result = store.list_validation_results("canvas")[0]
        provider = StaticDevelopmentIdentityProvider(
            identities=(
                DevelopmentIdentity("scientist", frozenset({ApprovalRole.SCIENTIST}), hash_development_credential("s")),
                DevelopmentIdentity("lead", frozenset({ApprovalRole.LAB_LEAD}), hash_development_credential("l")),
            ),
            clock=lambda: now,
        )
        for approval_id, role, secret in (
            ("scientist", ApprovalRole.SCIENTIST, "s"),
            ("lead", ApprovalRole.LAB_LEAD, "l"),
        ):
            await submit_approval(
                canvas_id="canvas",
                current_proposal_hash=result.proposal_hash,
                current_validation_result_hash=hash_validation_result(result),
                submission=ApprovalSubmission(
                    approval_id=approval_id,
                    required_role=role,
                    decision=ApprovalDecision.APPROVE,
                    rationale="Reviewed.",
                    credential=SecretStr(secret),
                ),
                state_store=store,
                identity_provider=provider,
            )

    snapshot = {"ideas_needing_setup": [], "setups_needing_run": [], "loops": [], **workflow}
    mcp = FakeMCP(note_text=seed, workflow=snapshot)
    settings = _workflow_settings()
    # Enable wet-lab execution for setups_needing_run path
    if "setups_needing_run" in workflow:
        settings.wet_lab_execution_enabled = True
    inner = OvershootAdapter(structured)
    governed = _governed(store, settings, inner)

    await process_once(mcp, governed, settings, store, "runtime", "canvas", governed.context)

    generated = [row for key, row in mcp.notes.items() if key not in seed]
    assert [row["title"].split(" #", 1)[0] for row in generated] == ["[EXP:Closed] after v001"]
    # Governance closures don't create connectors; they're terminal nodes
    assert inner.schema_calls == expected_schema_calls
