"""Regression tests for material-term policy and blocker normalization."""

from lab_agent.acronyms import AcronymDictionary
from lab_agent.evidence import EvidenceLedger
from lab_agent.grounding import evaluate_grounding
from lab_agent.models.evidence import (
    AcronymFlag,
    EvidenceCitation,
    EvidenceStatus,
    GroundingDecision,
)
from lab_agent.models.experiment import ExperimentSetup

EMPTY_DICT = AcronymDictionary(version=0, entries={})
SD_DICT = AcronymDictionary(version=1, entries={"SD": ("standard deviation",)})


def _setup(**kw) -> ExperimentSetup:
    base: dict = {"rationale": "because", "steps": ["mix A and B"], "inputs": ["A", "B"]}
    base.update(kw)
    return ExperimentSetup.model_validate(base)


def test_operationally_defined_lowercase_signal_does_not_block():
    ledger = EvidenceLedger()
    real_id = ledger.add("get_note", {"note_id": "n1"}, "validated fluorescence assay")
    setup = _setup(
        expected_readouts=[
            "Signal is normalized fluorescence intensity in arbitrary units, averaged "
            "across three wells at 30 minutes."
        ],
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id=real_id)],
    )

    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT)

    assert verdict.decision is GroundingDecision.EXECUTABLE


def test_explicit_unresolved_signal_still_requires_input():
    ledger = EvidenceLedger()
    real_id = ledger.add("get_note", {"note_id": "n1"}, "validated assay")
    setup = _setup(
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id=real_id)],
        ambiguity_flags=[AcronymFlag(term="signal", resolved=False)],
    )

    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT)

    assert verdict.decision is GroundingDecision.NEEDS_INPUT
    assert verdict.blocking_terms == ("signal",)


def test_flag_case_and_whitespace_deduplicate_against_detected_acronym():
    ledger = EvidenceLedger()
    real_id = ledger.add("get_note", {"note_id": "n1"}, "validated assay")
    setup = _setup(
        rationale="Report SD for each group.",
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id=real_id)],
        ambiguity_flags=[AcronymFlag(term=" sd ", resolved=False)],
    )

    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT)

    assert verdict.blocking_terms == ("SD",)
    assert verdict.reason == "unresolved term(s): SD"


def test_trimmed_flag_uses_normalized_term_for_dictionary_resolution():
    ledger = EvidenceLedger()
    real_id = ledger.add("get_note", {"note_id": "n1"}, "validated assay")
    setup = _setup(
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id=real_id)],
        ambiguity_flags=[AcronymFlag(term=" SD ", resolved=True)],
    )

    verdict = evaluate_grounding(setup, ledger, SD_DICT)

    assert verdict.decision is GroundingDecision.EXECUTABLE


def test_inline_definition_does_not_bypass_citation_validation():
    setup = _setup(
        rationale="Compare groups using standard deviation (SD).",
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id="fabricated")],
    )

    verdict = evaluate_grounding(setup, EvidenceLedger(), EMPTY_DICT, idea_text="compare SD")

    assert verdict.decision is GroundingDecision.INVALID_CITATION
