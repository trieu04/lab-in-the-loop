"""Tests for the deterministic grounding gate (plan item 7): sufficiency +
citations + no blocking ambiguity decide EXECUTABLE vs NEEDS_INPUT vs
INVALID_CITATION, and the audit trail stays minimal (plan item 11)."""

from __future__ import annotations

import hashlib
import json

import pytest

from lab_agent.acronyms import AcronymDictionary
from lab_agent.evidence import AUDIT_BYTE_CAP, EvidenceLedger
from lab_agent.grounding import evaluate_grounding, record_grounding_audit
from lab_agent.models.evidence import (
    AcronymFlag,
    EvidenceCitation,
    EvidenceStatus,
    GroundingDecision,
)
from lab_agent.models.experiment import ExperimentSetup
from lab_agent.state_store import StateStore

EMPTY_DICT = AcronymDictionary(version=0, entries={})
APPROVED_DICT = AcronymDictionary(version=1, entries={"PCR": ("Polymerase Chain Reaction",)})


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "state.db")
    yield s
    s.close()


def _setup(**kw) -> ExperimentSetup:
    base: dict = {"rationale": "because", "steps": ["mix A and B"], "inputs": ["A", "B"]}
    base.update(kw)
    return ExperimentSetup.model_validate(base)


def test_needs_input_when_evidence_status_is_unset():
    setup = _setup()  # evidence_status defaults to None
    verdict = evaluate_grounding(setup, EvidenceLedger(), EMPTY_DICT)
    assert verdict.decision is GroundingDecision.NEEDS_INPUT
    assert "sufficient" in verdict.reason


def test_needs_input_when_evidence_status_is_explicitly_insufficient():
    setup = _setup(evidence_status=EvidenceStatus.INSUFFICIENT)
    verdict = evaluate_grounding(setup, EvidenceLedger(), EMPTY_DICT)
    assert verdict.decision is GroundingDecision.NEEDS_INPUT


def test_invalid_citation_when_sufficient_but_zero_citations():
    setup = _setup(evidence_status=EvidenceStatus.SUFFICIENT, citations=[])
    verdict = evaluate_grounding(setup, EvidenceLedger(), EMPTY_DICT)
    assert verdict.decision is GroundingDecision.INVALID_CITATION


def test_invalid_citation_when_sufficient_but_fabricated_citation():
    setup = _setup(
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id="not-in-ledger")],
    )
    verdict = evaluate_grounding(setup, EvidenceLedger(), EMPTY_DICT)
    assert verdict.decision is GroundingDecision.INVALID_CITATION


def test_invalid_citation_when_mix_of_valid_and_fabricated():
    ledger = EvidenceLedger()
    real_id = ledger.add("get_note", {"note_id": "n1"}, "evidence text")
    setup = _setup(
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id=real_id), EvidenceCitation(source_id="fabricated")],
    )
    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT)
    assert verdict.decision is GroundingDecision.INVALID_CITATION


def test_executable_when_sufficient_and_all_citations_resolve():
    ledger = EvidenceLedger()
    real_id = ledger.add("get_note", {"note_id": "n1"}, "evidence text")
    setup = _setup(
        evidence_status=EvidenceStatus.SUFFICIENT, citations=[EvidenceCitation(source_id=real_id)],
    )
    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT)
    assert verdict.decision is GroundingDecision.EXECUTABLE


def test_blocking_term_wins_even_with_valid_citations():
    """An unresolved acronym-like term must force NEEDS_INPUT even though the
    setup is otherwise sufficient with a valid citation (plan item 7's stated
    precedence: ambiguity is checked before citations)."""
    ledger = EvidenceLedger()
    real_id = ledger.add("get_note", {"note_id": "n1"}, "evidence text")
    setup = _setup(
        rationale="Use PCR to amplify the sample.",
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id=real_id)],
        ambiguity_flags=[AcronymFlag(term="PCR", resolved=False)],
    )
    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT)  # empty dict: PCR unresolved
    assert verdict.decision is GroundingDecision.NEEDS_INPUT
    assert "PCR" in verdict.reason
    assert verdict.blocking_terms == ("PCR",)


def test_resolved_acronym_against_approved_dictionary_does_not_block():
    ledger = EvidenceLedger()
    real_id = ledger.add("get_note", {"note_id": "n1"}, "evidence text")
    setup = _setup(
        rationale="Use PCR to amplify the sample.",
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id=real_id)],
        ambiguity_flags=[AcronymFlag(term="PCR", resolved=True, expansion="Polymerase Chain Reaction")],
    )
    verdict = evaluate_grounding(setup, ledger, APPROVED_DICT)  # dictionary actually resolves PCR
    assert verdict.decision is GroundingDecision.EXECUTABLE


def test_unflagged_acronym_like_term_is_still_detected_and_blocks():
    """The gate re-scans the setup's own text -- a model that used an
    acronym-like term without self-flagging it must still be caught."""
    ledger = EvidenceLedger()
    real_id = ledger.add("get_note", {"note_id": "n1"}, "evidence text")
    setup = _setup(
        rationale="Use PCR to amplify the sample.",
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id=real_id)],
        ambiguity_flags=[],  # model never flagged it
    )
    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT)
    assert verdict.decision is GroundingDecision.NEEDS_INPUT
    assert "PCR" in verdict.blocking_terms


@pytest.mark.parametrize(
    "field", ["success_criteria", "parameters", "expected_readouts", "constraints"],
)
def test_unresolved_acronym_isolated_in_each_field_blocks(field):
    """D1 regression: an unresolved acronym-like term confined to any single
    free-text field -- including the 4 fields previously omitted from the
    re-scan (success_criteria/parameters/expected_readouts/constraints) --
    must still force NEEDS_INPUT, even when the model never self-flags it."""
    ledger = EvidenceLedger()
    real_id = ledger.add("get_note", {"note_id": "n1"}, "evidence text")
    setup = _setup(
        **{field: ["PCR yield exceeds threshold"]},
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id=real_id)],
        ambiguity_flags=[],  # model never flagged it
    )
    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT)
    assert verdict.decision is GroundingDecision.NEEDS_INPUT
    assert "PCR" in verdict.blocking_terms


def test_placeholder_single_letters_never_trigger_blocking():
    """Fixtures/tests routinely use "A"/"B" as material placeholders -- these
    must never be mistaken for unresolved acronyms."""
    ledger = EvidenceLedger()
    real_id = ledger.add("get_note", {"note_id": "n1"}, "evidence text")
    setup = _setup(
        rationale="Combine A with B.", steps=["mix A and B"], inputs=["A", "B"],
        evidence_status=EvidenceStatus.SUFFICIENT, citations=[EvidenceCitation(source_id=real_id)],
    )
    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT)
    assert verdict.decision is GroundingDecision.EXECUTABLE


def test_record_grounding_audit_is_minimal_and_under_byte_cap(store):
    ledger = EvidenceLedger()
    real_id = ledger.add("get_note", {"note_id": "n1"}, "some sensitive-looking evidence body")
    setup = _setup(
        evidence_status=EvidenceStatus.SUFFICIENT, citations=[EvidenceCitation(source_id=real_id)],
    )
    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT)

    record_grounding_audit(store, "c", verdict, ledger)

    events = store.list_audit_events("c")
    grounding_events = [e for e in events if e.event == "grounding_evaluated"]
    assert len(grounding_events) == 1
    payload = grounding_events[0].payload
    assert payload["decision"] == "executable"
    assert payload["reason"]
    expected_hash = hashlib.sha256(b"some sensitive-looking evidence body").hexdigest()
    assert payload["evidence"] == [{"source_id": real_id, "tool": "get_note", "content_hash": expected_hash}]

    blob = json.dumps(payload)
    assert "sensitive-looking evidence body" not in blob  # no raw content leaked
    assert len(json.dumps(payload["evidence"]).encode("utf-8")) <= AUDIT_BYTE_CAP
