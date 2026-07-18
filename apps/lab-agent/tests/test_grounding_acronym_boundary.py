"""D1 regression: the acronym gate's boundary must cover the original idea
text and retrieved evidence excerpts, not only the emitted setup.

Before this fix, ``evaluate_grounding`` only re-scanned the model's own
emitted setup fields; a model could see an unresolved acronym in the idea or
in what it read via a tool call, simply never write that term into the
setup, and still pass the gate as EXECUTABLE. See
``lab_agent.grounding._boundary_text``.
"""

from __future__ import annotations

from lab_agent.acronyms import AcronymDictionary
from lab_agent.evidence import EvidenceLedger
from lab_agent.grounding import evaluate_grounding
from lab_agent.models.evidence import EvidenceCitation, EvidenceStatus, GroundingDecision
from lab_agent.models.experiment import ExperimentSetup

EMPTY_DICT = AcronymDictionary(version=0, entries={})


def _setup(**kw) -> ExperimentSetup:
    base: dict = {"rationale": "because", "steps": ["mix A and B"], "inputs": ["A", "B"]}
    base.update(kw)
    return ExperimentSetup.model_validate(base)


def test_needs_input_when_unresolved_acronym_only_in_idea_text():
    ledger = EvidenceLedger()
    real_id = ledger.add("get_note", {"note_id": "n1"}, "evidence text")
    setup = _setup(
        evidence_status=EvidenceStatus.SUFFICIENT, citations=[EvidenceCitation(source_id=real_id)],
    )  # setup itself never mentions BIA
    verdict = evaluate_grounding(
        setup, ledger, EMPTY_DICT, idea_text="{idea: optimize BIA response in assay}",
    )
    assert verdict.decision is GroundingDecision.NEEDS_INPUT
    assert "BIA" in verdict.blocking_terms


def test_needs_input_when_unresolved_acronym_only_in_retrieved_evidence():
    ledger = EvidenceLedger()
    real_id = ledger.add("get_note", {"note_id": "n1"}, "Internal note mentions BIA without expansion")
    setup = _setup(
        evidence_status=EvidenceStatus.SUFFICIENT, citations=[EvidenceCitation(source_id=real_id)],
    )  # setup itself never mentions BIA, and the idea doesn't either
    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT, idea_text="optimize response in assay")
    assert verdict.decision is GroundingDecision.NEEDS_INPUT
    assert "BIA" in verdict.blocking_terms


def test_unresolved_acronym_in_uncited_evidence_still_blocks():
    """Retrieved evidence is scanned whether or not the model cited it --
    the gate must not trust the model's own choice of what to cite as the
    boundary of what it "saw"."""
    ledger = EvidenceLedger()
    cited_id = ledger.add("get_note", {"note_id": "n1"}, "clean evidence")
    ledger.add("get_note", {"note_id": "n2"}, "aside note referencing BIA")  # never cited
    setup = _setup(
        evidence_status=EvidenceStatus.SUFFICIENT, citations=[EvidenceCitation(source_id=cited_id)],
    )
    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT, idea_text="optimize response in assay")
    assert verdict.decision is GroundingDecision.NEEDS_INPUT
    assert "BIA" in verdict.blocking_terms


def test_approved_acronym_in_idea_and_evidence_does_not_block():
    dictionary = AcronymDictionary(version=1, entries={"BIA": ("Bioimpedance Analysis",)})
    ledger = EvidenceLedger()
    real_id = ledger.add("get_note", {"note_id": "n1"}, "Internal note mentions BIA context")
    setup = _setup(
        evidence_status=EvidenceStatus.SUFFICIENT, citations=[EvidenceCitation(source_id=real_id)],
    )
    verdict = evaluate_grounding(
        setup, ledger, dictionary, idea_text="{idea: optimize BIA response in assay}",
    )
    assert verdict.decision is GroundingDecision.EXECUTABLE


def test_no_idea_text_given_falls_back_to_setup_and_evidence_only():
    """``idea_text`` is optional (keyword-only, defaulted) -- omitting it
    must not change existing setup/evidence scanning behavior."""
    ledger = EvidenceLedger()
    real_id = ledger.add("get_note", {"note_id": "n1"}, "evidence text")
    setup = _setup(
        rationale="Combine A with B.",
        evidence_status=EvidenceStatus.SUFFICIENT, citations=[EvidenceCitation(source_id=real_id)],
    )
    verdict = evaluate_grounding(setup, ledger, EMPTY_DICT)
    assert verdict.decision is GroundingDecision.EXECUTABLE
