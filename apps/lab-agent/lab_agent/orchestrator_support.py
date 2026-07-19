"""Write stages the experiment-loop orchestrator delegates to.

Each *write* helper places an already-validated generated node on the canvas as
a capability-protected **Browser** widget backed by the canonical
:class:`~lab_agent.artifact_store.ArtifactStore` (see
:mod:`lab_agent.durable_browser`). Ideas and human-authored input stay Notes;
only these system-generated Setup/Result/Closed/Needs Input artifacts become
Browser widgets. A needs-input *prompt* is one of these generated artifacts;
the human's *response* to it is a plain Note this module never writes.

The literal title markers and note-body first-line fragments (``Idea: {idea_id}``
etc.) are composed here on purpose: ``scripts/check-workflow-contract-parity.py``
greps this module for them verbatim, so they must stay physically present, not
move into the durable layer. Each fragment is also carried into the canonical
payload as the model-facing rendering, so a later round can read a Browser-backed
stage's text from the store rather than from Browser HTML.

The emit stages (model reads/emits a structured node) live in
:mod:`lab_agent.orchestrator_emit`; the needs-input write stage lives in
:mod:`lab_agent.orchestrator_needs_input`. Both are re-exported here for the
orchestrator's single import site.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lab_agent import durable_browser, nodes, render
from lab_agent.config import Settings
from lab_agent.mcp_client import MCPClient
from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
from lab_agent.models.experiment import ExperimentResult, ExperimentSetup, LoopDecision
from lab_agent.models.states import DecisionState
from lab_agent.orchestrator_emit import (
    SchemaValidationError,
    coerce_or_fail,
    emit_decision,
    emit_result,
    ground_and_emit_setup,
)
from lab_agent.orchestrator_needs_input import write_needs_input_node
from lab_agent.state_store import StateStore


def version(round_index: int) -> str:
    """Format a round index as a zero-padded version tag, e.g. 1 -> 'v001'."""
    return f"v{round_index:03d}"


@dataclass
class LoopSummary:
    """Outcome of running one experiment loop to termination."""

    rounds: int = 0
    stopped_reason: str = ""
    setup_ids: list[str] = field(default_factory=list)
    result_ids: list[str] = field(default_factory=list)
    closed_id: str = ""


# ── Write stages (durable: canonical artifact + Browser widget, then reconcile) ──
async def write_setup_node(
    mcp: MCPClient,
    store: StateStore,
    settings: Settings,
    *,
    canvas_id: str,
    setup: ExperimentSetup,
    idea_text: str,
    idea_id: str,
    round_index: int,
    predecessor_id: str,
    edge_kind: str,
    provenance: ArtifactProvenance | None = None,
) -> str:
    """Write an already-validated ExperimentSetup as a Browser artifact; return its id."""
    title = f"{nodes.EXP_SETUP} {version(round_index)}] {idea_text[:40]}"
    body = f"Idea: {idea_id}\nRound: {round_index}\n\n{render.render_setup(setup)}"
    payload = {
        **setup.model_dump(), "title": title, "round": round_index, "idea_id": idea_id,
        durable_browser.RENDERED_TEXT_KEY: body,
    }
    return await durable_browser.write_artifact_browser_durable(
        mcp, store, settings, canvas_id=canvas_id, artifact_type=ArtifactType.SETUP,
        state=DecisionState.RUNNING, title=title, payload=payload,
        provenance=provenance or durable_browser.provenance_for(
            settings,
            source_widget_id=idea_id,
            trigger_id=f"setup/predecessor:{predecessor_id}/round:{round_index}",
        ),
        discriminator=f"setup/predecessor:{predecessor_id}/round:{round_index}",
        round_index=round_index, predecessor_id=predecessor_id, edge_kind=edge_kind,
    )


async def write_result_node(
    mcp: MCPClient,
    store: StateStore,
    settings: Settings,
    *,
    canvas_id: str,
    result: ExperimentResult,
    setup_id: str,
    robot_id: str,
    round_index: int,
    provenance: ArtifactProvenance | None = None,
) -> str:
    """Write an already-validated ExperimentResult as a Browser artifact; return its id."""
    title = f"{nodes.EXP_RESULT} {version(round_index)}]"
    body = f"Setup: {setup_id}\nRound: {round_index}\n\n{render.render_result(result)}"
    payload = {
        **result.model_dump(), "title": title, "round": round_index, "setup_id": setup_id,
        durable_browser.RENDERED_TEXT_KEY: body,
    }
    return await durable_browser.write_artifact_browser_durable(
        mcp, store, settings, canvas_id=canvas_id, artifact_type=ArtifactType.RESULT,
        state=DecisionState.ANALYSIS_COMPLETE, title=title, payload=payload,
        provenance=provenance or durable_browser.provenance_for(
            settings,
            source_widget_id=setup_id,
            trigger_id=f"result/setup:{setup_id}/round:{round_index}",
        ),
        discriminator=f"result/setup:{setup_id}/round:{round_index}",
        round_index=round_index, predecessor_id=robot_id, edge_kind="robot_result",
    )


async def write_closed_node(
    mcp: MCPClient,
    store: StateStore,
    settings: Settings,
    *,
    canvas_id: str,
    decision: LoopDecision,
    reason: str,
    backstop: bool,
    round_index: int,
    result_id: str = "",
    predecessor_id: str = "",
    edge_kind: str = "result_closed",
    discriminator: str = "",
    provenance: ArtifactProvenance | None = None,
) -> str:
    """Write one terminal [EXP:Closed] Browser artifact from its predecessor."""
    predecessor = predecessor_id or result_id
    default_lineage = (
        f"closed/result:{result_id}/round:{round_index}"
        if result_id and not predecessor_id
        else f"closed/predecessor:{predecessor}/round:{round_index}"
    )
    body = render.render_decision(decision) + (f"\n\n({reason})" if backstop else "")
    title = f"{nodes.CLOSED} after {version(round_index)}"
    payload = {
        **decision.model_dump(), "title": title, "round": round_index, "reason": reason,
        durable_browser.RENDERED_TEXT_KEY: body,
    }
    return await durable_browser.write_artifact_browser_durable(
        mcp, store, settings, canvas_id=canvas_id, artifact_type=ArtifactType.CLOSED,
        state=DecisionState.CLOSED, title=title, payload=payload,
        provenance=provenance or durable_browser.provenance_for(
            settings,
            source_widget_id=predecessor,
            trigger_id=default_lineage,
        ),
        discriminator=discriminator or default_lineage,
        round_index=round_index, predecessor_id=predecessor, edge_kind=edge_kind,
    )


__all__ = [
    "LoopSummary", "SchemaValidationError", "coerce_or_fail", "emit_decision",
    "emit_result", "ground_and_emit_setup", "version", "write_closed_node",
    "write_needs_input_node", "write_result_node", "write_setup_node",
]
