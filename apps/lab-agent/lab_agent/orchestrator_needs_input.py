"""The ``[EXP:Needs Input]`` write stage: an on-demand generated prompt artifact.

Split from :mod:`lab_agent.orchestrator_support` (which keeps the setup/
result/closed write stages) to hold each source file under the 200-line
budget. See that module's docstring for the shared write-stage contract
(Browser widget backed by :class:`~lab_agent.artifact_store.ArtifactStore`;
ideas/human input stay Notes).
"""

from __future__ import annotations

import hashlib
from typing import Any

from lab_agent import durable_browser, nodes
from lab_agent.config import Settings
from lab_agent.mcp_client import MCPClient
from lab_agent.models.artifact import ArtifactType
from lab_agent.models.states import DecisionState
from lab_agent.state_store import StateStore


def compute_reason_hash(reason: str) -> str:
    """A stable, short id for a needs-input ``reason`` string.

    Used as the dedup identity alongside ``(canvas, predecessor)`` so a
    restart/repeat with the *same* reason converges on one artifact/widget/
    connector, while a genuinely *different* reason gets its own -- the
    round only affects title/layout, never identity (plan item 8).
    """
    return hashlib.sha256(reason.encode("utf-8")).hexdigest()[:12]


async def write_needs_input_node(
    mcp: MCPClient,
    store: StateStore,
    settings: Settings,
    *,
    canvas_id: str,
    message: str,
    reason: str,
    context: dict[str, Any] | None = None,
    round_index: int,
    predecessor_id: str,
    edge_kind: str,
) -> str:
    """Write a ``[EXP:Needs Input]`` Browser artifact requesting a human decision.

    The generated prompt/status artifact is a Browser widget backed by
    ArtifactStore, written under :data:`~lab_agent.models.states.DecisionState.NEEDS_REVIEW`
    (a safe, non-terminal state) exactly like :func:`~lab_agent.orchestrator_support.write_setup_node`
    et al. -- the human's *response* stays a plain Note the orchestrator reads
    separately; this helper never creates or edits that Note.

    Callable on demand (e.g. an ambiguous grounding or an approval gate); it
    is deliberately **not** wired into a schema-validation failure path --
    that must keep writing nothing per the existing fail-closed contract
    (see :mod:`lab_agent.orchestrator`'s ``_fail_closed``).
    """
    body = f"{message}\n\n({reason})" if reason else message
    title = f"{nodes.EXP_NEEDS_INPUT} round {round_index}"
    reason_hash = compute_reason_hash(reason)
    payload = {
        "message": message, "reason": reason, "reason_hash": reason_hash, "context": context or {},
        "title": title, "round": round_index,
        durable_browser.RENDERED_TEXT_KEY: body,
    }
    return await durable_browser.write_artifact_browser_durable(
        mcp, store, settings, canvas_id=canvas_id, artifact_type=ArtifactType.NEEDS_INPUT,
        state=DecisionState.NEEDS_REVIEW, title=title, payload=payload,
        provenance=durable_browser.provenance_for(
            settings,
            source_widget_id=predecessor_id,
            trigger_id=f"needs_input/predecessor:{predecessor_id}/reason:{reason_hash}",
        ),
        discriminator=f"needs_input/predecessor:{predecessor_id}/reason:{reason_hash}",
        round_index=round_index, predecessor_id=predecessor_id, edge_kind=edge_kind,
    )


__all__ = ["compute_reason_hash", "write_needs_input_node"]
