"""The idea -> setup entry point, split out of :mod:`lab_agent.orchestrator`
to hold that module under the 200-line budget.

Grounds on the RagCluster + idea, grades the result against a fresh
:class:`~lab_agent.evidence.EvidenceLedger` and the approved acronym
dictionary (see :mod:`lab_agent.grounding`), and only writes an
``ExperimentSetup`` node when the grounding gate says it is executable.
"""

from __future__ import annotations

from lab_agent.adapters.base import ModelAdapter
from lab_agent.config import Settings
from lab_agent.evidence import EvidenceLedger
from lab_agent.grounding import (
    SetupOutcome,
    evaluate_grounding,
    record_grounding_audit,
    write_needs_input_for_verdict,
)
from lab_agent.mcp_client import MCPClient
from lab_agent.models.evidence import GroundingDecision
from lab_agent.orchestrator_support import ground_and_emit_setup, write_setup_node
from lab_agent.state_store import StateStore

_SCHEMA_FAILED = SetupOutcome("", None, GroundingDecision.SCHEMA_FAILED, "schema validation failed")


async def generate_setup(
    mcp: MCPClient,
    adapter: ModelAdapter,
    settings: Settings,
    store: StateStore,
    *,
    canvas_id: str,
    idea_text: str,
    idea_id: str,
    ragcluster_id: str,
    round_index: int,
    prior: str = "",
) -> SetupOutcome:
    """Ground on the RagCluster + idea, grade it against the evidence ledger and
    acronym dictionary, and emit an ExperimentSetup node only when the
    grounding gate says it is executable.

    Fail-closed: a malformed emit writes nothing (SCHEMA_FAILED, retryable);
    insufficient evidence or a blocking ambiguity writes only a
    ``[EXP:Needs Input]`` prompt (NEEDS_INPUT, not a schema failure); a
    sufficiency claim with zero/fabricated/mixed-invalid citations writes
    nothing and is retryable exactly like a schema failure (INVALID_CITATION).
    """
    ledger = EvidenceLedger()
    setup = await ground_and_emit_setup(
        mcp, adapter, settings, canvas_id=canvas_id, idea_text=idea_text,
        ragcluster_id=ragcluster_id, prior=prior, ledger=ledger,
    )
    if setup is None:
        return _SCHEMA_FAILED

    verdict = evaluate_grounding(setup, ledger, idea_text=idea_text)
    record_grounding_audit(store, canvas_id, verdict, ledger)

    if verdict.decision is GroundingDecision.NEEDS_INPUT:
        await write_needs_input_for_verdict(
            mcp, store, settings, canvas_id=canvas_id, verdict=verdict, round_index=round_index,
            predecessor_id=idea_id, edge_kind="idea_setup",
        )
        return SetupOutcome("", None, verdict.decision, verdict.reason)
    if verdict.decision is GroundingDecision.INVALID_CITATION:
        return SetupOutcome("", None, verdict.decision, verdict.reason)

    setup_id = await write_setup_node(
        mcp, store, settings, canvas_id=canvas_id, setup=setup, idea_text=idea_text, idea_id=idea_id,
        round_index=round_index, predecessor_id=idea_id, edge_kind="idea_setup",
    )
    return SetupOutcome(setup_id, setup, verdict.decision, verdict.reason)


__all__ = ["generate_setup"]
