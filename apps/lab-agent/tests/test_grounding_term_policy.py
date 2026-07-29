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
COLLIDING_DICT = AcronymDictionary(
    version=1,
    entries={"CT": ("computed tomography", "cycle threshold")},
)


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


def test_glossary_like_lowercase_flag_does_not_require_input():
    ledger = EvidenceLedger()
    real_id = ledger.add("get_note", {"note_id": "n1"}, "validated assay")
    setup = _setup(
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id=real_id)],
        ambiguity_flags=[AcronymFlag(term="signal", resolved=False)],
    )

    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT)

    assert verdict.decision is GroundingDecision.EXECUTABLE


def test_material_lowercase_flag_still_requires_input():
    ledger = EvidenceLedger()
    real_id = ledger.add("get_note", {"note_id": "n1"}, "validated assay")
    setup = _setup(
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id=real_id)],
        ambiguity_flags=[
            AcronymFlag(
                term="signal",
                material_impact="Changes the assay interpretation.",
                alternatives=["fluorescence", "luminescence"],
            )
        ],
    )

    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT)

    assert verdict.decision is GroundingDecision.NEEDS_INPUT
    assert verdict.blocking_terms == ("signal",)


def test_material_flag_requires_two_distinct_nonblank_alternatives():
    ledger = EvidenceLedger()
    real_id = ledger.add("get_note", {"note_id": "n1"}, "validated assay")
    setup = _setup(
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id=real_id)],
        ambiguity_flags=[
            AcronymFlag(
                term="signal",
                material_impact="Changes the assay interpretation.",
                alternatives=["fluorescence", " fluorescence ", ""],
            )
        ],
    )

    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT)

    assert verdict.decision is GroundingDecision.EXECUTABLE


def test_artifact_glossary_terms_do_not_create_needs_input():
    ledger = EvidenceLedger()
    real_id = ledger.add("get_note", {"note_id": "n1"}, "public synthetic evidence")
    setup = _setup(
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id=real_id)],
        ambiguity_flags=[
            AcronymFlag(term="candidate alpha"),
            AcronymFlag(term="lung stiffness score"),
            AcronymFlag(term="synthetic"),
        ],
    )

    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT)

    assert verdict.decision is GroundingDecision.EXECUTABLE
    assert verdict.blocking_terms == ()


def test_flag_only_lowercase_dictionary_acronym_remains_blocking_when_colliding():
    ledger = EvidenceLedger()
    real_id = ledger.add("get_note", {"note_id": "n1"}, "validated assay")
    setup = _setup(
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id=real_id)],
        ambiguity_flags=[AcronymFlag(term="ct")],
    )

    verdict = evaluate_grounding(setup, ledger, COLLIDING_DICT)

    assert verdict.decision is GroundingDecision.NEEDS_INPUT
    assert verdict.blocking_terms == ("CT",)


def test_short_lowercase_glossary_flags_are_advisory():
    ledger = EvidenceLedger()
    real_id = ledger.add("get_note", {"note_id": "n1"}, "validated assay")
    setup = _setup(
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id=real_id)],
        ambiguity_flags=[
            AcronymFlag(term="dose"),
            AcronymFlag(term="cell"),
            AcronymFlag(term="time"),
            AcronymFlag(term="data"),
            AcronymFlag(term="pcr"),
        ],
    )

    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT)

    assert verdict.decision is GroundingDecision.EXECUTABLE


def test_flag_only_mixed_case_acronym_remains_blocking():
    ledger = EvidenceLedger()
    real_id = ledger.add("get_note", {"note_id": "n1"}, "validated assay")
    setup = _setup(
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id=real_id)],
        ambiguity_flags=[AcronymFlag(term="qPCR")],
    )

    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT)

    assert verdict.decision is GroundingDecision.NEEDS_INPUT
    assert verdict.blocking_terms == ("qPCR",)


def test_unknown_lowercase_structured_flags_are_advisory():
    ledger = EvidenceLedger()
    real_id = ledger.add("get_note", {"note_id": "n1"}, "validated assay")
    setup = _setup(
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id=real_id)],
        ambiguity_flags=[
            AcronymFlag(term="real-time"),
            AcronymFlag(term="dose-time"),
            AcronymFlag(term="day-1"),
            AcronymFlag(term="alpha-pcr"),
        ],
    )

    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT)

    assert verdict.decision is GroundingDecision.EXECUTABLE


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
