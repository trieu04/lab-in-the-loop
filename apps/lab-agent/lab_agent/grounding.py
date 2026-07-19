"""The Phase 4 grounding gate: decide whether a generated setup is executable.

Precedence (plan item 7): an explicit ``insufficient``/missing evidence
status, or a blocking (unresolved) acronym-like term, always yields
NEEDS_INPUT -- even if citations happen to be valid, a human should be asked
rather than the loop silently degrading. Only once evidence is claimed
sufficient AND no term is blocking do citations get checked; a claim of
sufficiency with zero, fabricated, or mixed-invalid citations is
INVALID_CITATION, handled exactly like a schema failure (no write, durable
failed attempt eligible for retry/quarantine -- see ``lab_agent.watch``).
"""

from __future__ import annotations

from dataclasses import dataclass

from lab_agent.acronyms import AcronymDictionary, detect_acronym_terms, load_acronym_dictionary
from lab_agent.config import Settings
from lab_agent.evidence import EvidenceLedger
from lab_agent.mcp_client import MCPClient
from lab_agent.models.evidence import EvidenceStatus, GroundingDecision
from lab_agent.models.experiment import ExperimentSetup
from lab_agent.orchestrator_needs_input import write_needs_input_node
from lab_agent.state_store import MAX_PAYLOAD_BYTES, StateStore, payload_size_bytes


@dataclass(frozen=True)
class GroundingVerdict:
    """One gate decision, with just enough detail to act on and audit."""

    decision: GroundingDecision
    reason: str
    blocking_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class SetupOutcome:
    """What ``generate_setup``/``run_loop`` produce for one setup attempt."""

    setup_id: str
    setup: ExperimentSetup | None
    decision: GroundingDecision
    reason: str = ""


def _source_text(setup: ExperimentSetup) -> str:
    return " ".join([
        setup.rationale,
        setup.hypothesis,
        *setup.steps,
        *setup.conditions,
        *setup.inputs,
        *setup.success_criteria,
        *setup.parameters,
        *setup.expected_readouts,
        *setup.constraints,
    ])


def _boundary_text(setup: ExperimentSetup, idea_text: str, ledger: EvidenceLedger) -> str:
    """All deterministic text the acronym gate must clear before any setup
    write: the original idea, the emitted setup fields, and every retrieved
    (not just cited) evidence excerpt still held by the ledger.

    Scanning only the emitted setup lets the model silently omit an
    unresolved acronym it saw in the idea or in retrieved evidence and still
    pass as EXECUTABLE -- this closes that gap.
    """
    return " ".join([idea_text, _source_text(setup), *ledger.excerpts()])


def _blocking_terms(
    setup: ExperimentSetup, idea_text: str, ledger: EvidenceLedger, dictionary: AcronymDictionary,
) -> tuple[str, ...]:
    """Terms that stay unresolved against the approved dictionary.

    Re-verifies both the model's self-reported ``ambiguity_flags`` and any
    acronym-like term the model didn't flag at all, across the full boundary
    (idea + setup + retrieved evidence) -- the dictionary is the ground
    truth, never the model's own ``resolved`` claim or its choice of what to
    quote into the setup.
    """
    detected = set(detect_acronym_terms(_boundary_text(setup, idea_text, ledger)))
    candidates = {flag.term for flag in setup.ambiguity_flags} | detected
    blocking = {term for term in candidates if not dictionary.resolve(term)[0]}
    return tuple(sorted(blocking))


def evaluate_grounding(
    setup: ExperimentSetup,
    ledger: EvidenceLedger,
    dictionary: AcronymDictionary | None = None,
    *,
    idea_text: str = "",
) -> GroundingVerdict:
    """Grade one generated setup against the current ledger + acronym dictionary.

    ``idea_text``, when given, is folded into the acronym-boundary scan
    alongside the emitted setup and every retrieved evidence excerpt in
    ``ledger`` -- see :func:`_boundary_text`.
    """
    if setup.evidence_status is not EvidenceStatus.SUFFICIENT:
        return GroundingVerdict(GroundingDecision.NEEDS_INPUT, "evidence not asserted sufficient")

    blocking = _blocking_terms(setup, idea_text, ledger, dictionary or load_acronym_dictionary())
    if blocking:
        return GroundingVerdict(
            GroundingDecision.NEEDS_INPUT, f"unresolved term(s): {', '.join(blocking)}", blocking,
        )

    valid, _known, _unknown = ledger.validate_citations([c.source_id for c in setup.citations])
    if not valid:
        return GroundingVerdict(GroundingDecision.INVALID_CITATION, "citations missing or unresolved in ledger")

    return GroundingVerdict(GroundingDecision.EXECUTABLE, "sufficient evidence, valid citations")


def record_grounding_audit(
    store: StateStore, canvas_id: str, verdict: GroundingVerdict, ledger: EvidenceLedger
) -> None:
    """Audit one grounding decision: ids/hashes/status/reason only (never raw
    evidence content, credentials, or capability URLs).

    ``ledger.audit_summary()`` only bounds its own nested rows; the enclosing
    payload (those rows plus ``decision``/``reason``) is what
    ``StateStore.append_audit_event`` actually caps at
    :data:`~lab_agent.state_store.MAX_PAYLOAD_BYTES`. A full ledger plus a
    long reason can otherwise exceed that whole-payload cap even though the
    nested rows alone stayed under it, raising before a needs-input artifact
    is ever written. So size the *final* canonical payload here and
    deterministically drop the newest evidence rows -- never the decision or
    reason -- until it fits.
    """
    decision = verdict.decision.value
    reason = verdict.reason[:200]
    rows = [
        {
            "source_id": row["source_id"],
            "tool": row["tool"],
            "content_hash": row["content_hash"],
        }
        for row in ledger.audit_summary()
    ]
    payload: dict[str, object] = {"decision": decision, "reason": reason, "evidence": rows}
    while rows and payload_size_bytes(payload) > MAX_PAYLOAD_BYTES:
        rows = rows[:-1]
        payload = {"decision": decision, "reason": reason, "evidence": rows}
    store.append_audit_event(canvas_id, "grounding_evaluated", payload)


async def write_needs_input_for_verdict(
    mcp: MCPClient,
    store: StateStore,
    settings: Settings,
    *,
    canvas_id: str,
    verdict: GroundingVerdict,
    round_index: int,
    predecessor_id: str,
    edge_kind: str,
    context: dict[str, object] | None = None,
) -> str:
    """Write the ``[EXP:Needs Input]`` prompt for a NEEDS_INPUT verdict."""
    message = f"This setup needs human input before it can run: {verdict.reason}"
    return await write_needs_input_node(
        mcp, store, settings, canvas_id=canvas_id, message=message, reason=verdict.reason,
        context=context, round_index=round_index, predecessor_id=predecessor_id, edge_kind=edge_kind,
    )


__all__ = [
    "GroundingVerdict",
    "SetupOutcome",
    "evaluate_grounding",
    "record_grounding_audit",
    "write_needs_input_for_verdict",
]
