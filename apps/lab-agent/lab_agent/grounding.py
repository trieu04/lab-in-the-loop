"""Decide whether a setup is executable: evidence, ambiguity, then citations.

Failures yield Needs Input or a retryable no-write invalid-citation outcome.
"""

from __future__ import annotations

from dataclasses import dataclass

from lab_agent.acronyms import (
    AcronymDictionary,
    detect_acronym_terms,
    detect_inline_acronym_definitions,
    load_acronym_dictionary,
)
from lab_agent.config import Settings
from lab_agent.evidence import EvidenceLedger
from lab_agent.mcp_client import MCPClient
from lab_agent.models.evidence import AcronymFlag, EvidenceStatus, GroundingDecision
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


def _source_parts(setup: ExperimentSetup) -> list[str]:
    return [
        setup.rationale,
        setup.hypothesis,
        *setup.steps,
        *setup.conditions,
        *setup.inputs,
        *setup.success_criteria,
        *setup.parameters,
        *setup.expected_readouts,
        *setup.constraints,
    ]


def _source_text(setup: ExperimentSetup) -> str:
    return " ".join(_source_parts(setup))


def _boundary_text(setup: ExperimentSetup, idea_text: str, ledger: EvidenceLedger) -> str:
    """All deterministic text the acronym gate must clear before any setup
    write: the original idea, the emitted setup fields, and every retrieved
    (not just cited) evidence excerpt still held by the ledger.

    Scanning only the emitted setup lets the model silently omit an
    unresolved acronym it saw in the idea or in retrieved evidence and still
    pass as EXECUTABLE -- this closes that gap.
    """
    return " ".join([idea_text, _source_text(setup), *ledger.excerpts()])


def _has_material_ambiguity(flag: AcronymFlag) -> bool:
    """Require explicit impact and two distinct choices for a non-acronym flag."""
    if not flag.material_impact.strip():
        return False
    alternatives = {
        alternative.strip().casefold()
        for alternative in flag.alternatives
        if alternative.strip()
    }
    return len(alternatives) >= 2


def _flag_acronym_terms(term: str, dictionary: AcronymDictionary) -> tuple[str, ...]:
    """Recognize explicit acronym flags without guessing from short lowercase words."""
    detected = detect_acronym_terms(term)
    if detected:
        return tuple(detected)
    normalized = term.upper()
    if normalized in dictionary.entries:
        return (normalized,)
    if any(character.isupper() for character in term[1:]) and term.isalnum():
        return (term,)
    return ()


def _blocking_terms(
    setup: ExperimentSetup, idea_text: str, ledger: EvidenceLedger, dictionary: AcronymDictionary,
) -> tuple[str, ...]:
    """Return terms unresolved by the dictionary or one local definition.

    Acronym-like terms remain governed by the deterministic boundary scan.
    Non-acronym flags block only when the provider supplies explicit material
    impact and at least two distinct alternatives; glossary-like flags are
    advisory and do not create a human-input dead end.
    """
    detected = detect_acronym_terms(_boundary_text(setup, idea_text, ledger))
    detected_by_key = {term.casefold(): term for term in detected}
    inline_definitions: dict[str, set[str]] = {}
    for part in _source_parts(setup):
        for term, expansions in detect_inline_acronym_definitions(part).items():
            inline_definitions.setdefault(term.upper(), set()).update(
                expansion.casefold() for expansion in expansions
            )
    blocking: dict[str, str] = {}

    for flag in setup.ambiguity_flags:
        term = flag.term.strip() or "<unspecified>"
        flag_acronyms = _flag_acronym_terms(term, dictionary)
        if flag_acronyms:
            for acronym in flag_acronyms:
                if not dictionary.resolve(acronym)[0]:
                    key = acronym.casefold()
                    blocking.setdefault(key, detected_by_key.get(key, acronym))
            continue
        if _has_material_ambiguity(flag) and not dictionary.resolve(term)[0]:
            key = term.casefold()
            blocking.setdefault(key, detected_by_key.get(key, term))

    for term in detected:
        definitions = inline_definitions.get(term.upper(), set())
        if len(definitions) > 1:
            blocking.setdefault(term.casefold(), term)
            continue
        if dictionary.resolve(term)[0] or len(definitions) == 1:
            continue
        blocking.setdefault(term.casefold(), term)

    return tuple(display for _key, display in sorted(blocking.items()))


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
