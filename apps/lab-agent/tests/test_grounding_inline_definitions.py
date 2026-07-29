"""Regression tests for setup-local acronym definition clearance."""

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


def _ledger() -> tuple[EvidenceLedger, str]:
    ledger = EvidenceLedger()
    source_id = ledger.add("get_note", {"note_id": "n1"}, "validated assay procedure")
    return ledger, source_id


def test_setup_local_inline_definition_clears_detected_acronym_from_idea():
    ledger, source_id = _ledger()
    setup = _setup(
        rationale="Compare groups using standard deviation (SD).",
        expected_readouts=["Normalized fluorescence intensity in arbitrary units at 30 minutes."],
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id=source_id)],
    )

    verdict = evaluate_grounding(
        setup, ledger, EMPTY_DICT, idea_text="{idea: compare the SD of the signal}",
    )

    assert verdict.decision is GroundingDecision.EXECUTABLE
    assert verdict.blocking_terms == ()


def test_definition_in_evidence_only_does_not_clear_detected_acronym():
    ledger = EvidenceLedger()
    source_id = ledger.add(
        "get_note", {"note_id": "n1"}, "Use standard deviation (SD) for variation.",
    )
    setup = _setup(
        expected_readouts=["Normalized fluorescence intensity in arbitrary units."],
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id=source_id)],
    )

    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT, idea_text="compare SD")

    assert verdict.decision is GroundingDecision.NEEDS_INPUT
    assert verdict.blocking_terms == ("SD",)


def test_explicit_material_flag_overrides_setup_local_definition():
    ledger, source_id = _ledger()
    setup = _setup(
        rationale="Compare groups using standard deviation (SD).",
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id=source_id)],
        ambiguity_flags=[AcronymFlag(term="SD", resolved=False)],
    )

    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT, idea_text="compare SD")

    assert verdict.decision is GroundingDecision.NEEDS_INPUT
    assert verdict.blocking_terms == ("SD",)


def test_conflicting_setup_local_definitions_remain_blocking():
    ledger, source_id = _ledger()
    setup = _setup(
        rationale="Report standard deviation (SD); exclude sudden death (SD) events.",
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id=source_id)],
    )

    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT, idea_text="compare SD")

    assert verdict.decision is GroundingDecision.NEEDS_INPUT
    assert verdict.blocking_terms == ("SD",)


def test_conflicting_definitions_override_unique_dictionary_resolution():
    ledger, source_id = _ledger()
    setup = _setup(
        rationale="Report standard deviation (SD); exclude sudden death (SD) events.",
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id=source_id)],
    )

    verdict = evaluate_grounding(setup, ledger, SD_DICT, idea_text="compare SD")

    assert verdict.decision is GroundingDecision.NEEDS_INPUT
    assert verdict.blocking_terms == ("SD",)


def test_definition_cannot_be_synthesized_across_scalar_fields():
    ledger, source_id = _ledger()
    setup = _setup(
        rationale="standard",
        hypothesis="deviation (SD)",
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id=source_id)],
    )

    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT, idea_text="compare SD")

    assert verdict.decision is GroundingDecision.NEEDS_INPUT
    assert verdict.blocking_terms == ("SD",)


def test_definition_cannot_be_synthesized_across_list_items():
    ledger, source_id = _ledger()
    setup = _setup(
        steps=["standard", "deviation (SD)"],
        evidence_status=EvidenceStatus.SUFFICIENT,
        citations=[EvidenceCitation(source_id=source_id)],
    )

    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT, idea_text="compare SD")

    assert verdict.decision is GroundingDecision.NEEDS_INPUT
    assert verdict.blocking_terms == ("SD",)
