"""Tests for the approved, versioned acronym dictionary (plan item 5):
approved-only resolution, unknown/colliding terms stay unresolved and
visible, an empty/minimal seed is acceptable, expansions are never invented."""

from __future__ import annotations

from pathlib import Path

import pytest

from lab_agent.acronyms import (
    AcronymDictionary,
    detect_acronym_terms,
    detect_inline_acronym_definitions,
    load_acronym_dictionary,
)


def test_load_acronym_dictionary_reads_packaged_resource():
    dictionary = load_acronym_dictionary()
    assert dictionary.version >= 1
    assert "PCR" in dictionary.entries


def test_resolve_returns_true_only_for_single_approved_candidate():
    dictionary = load_acronym_dictionary()
    resolved, expansion = dictionary.resolve("PCR")
    assert resolved is True
    assert expansion == "Polymerase Chain Reaction"


def test_resolve_is_case_insensitive():
    dictionary = load_acronym_dictionary()
    resolved, expansion = dictionary.resolve("pcr")
    assert resolved is True
    assert expansion == "Polymerase Chain Reaction"


def test_resolve_unknown_term_stays_unresolved_never_invented():
    dictionary = load_acronym_dictionary()
    resolved, expansion = dictionary.resolve("XYZ-NOT-A-REAL-TERM")
    assert resolved is False
    assert expansion == ""


def test_resolve_colliding_candidates_stays_unresolved():
    """A term with 2+ approved candidates (e.g. CT: Computed Tomography vs
    Cycle Threshold) must surface as unresolved, never guess one."""
    dictionary = load_acronym_dictionary()
    resolved, expansion = dictionary.resolve("CT")
    assert resolved is False
    assert expansion == ""


def test_empty_dictionary_resolves_nothing_but_does_not_fail():
    dictionary = AcronymDictionary(version=0, entries={})
    resolved, expansion = dictionary.resolve("PCR")
    assert resolved is False
    assert expansion == ""


def test_load_acronym_dictionary_missing_resource_falls_back_to_empty_seed(tmp_path: Path):
    """A missing/misconfigured resource file must not fail closed the entire
    loader -- it degrades to an empty, always-unresolved dictionary."""
    missing = tmp_path / "does-not-exist.json"
    dictionary = load_acronym_dictionary(missing)
    assert dictionary.version == 0
    assert dictionary.entries == {}
    assert dictionary.resolve("PCR") == (False, "")


def test_detect_acronym_terms_finds_uppercase_tokens_and_dedupes():
    terms = detect_acronym_terms("Run PCR on the sample, then repeat PCR and check DNA.")
    assert terms == ["PCR", "DNA"]  # first-seen order, deduplicated


def test_detect_acronym_terms_ignores_single_letters_and_lowercase():
    terms = detect_acronym_terms("Combine A with B to test PCR yield.")
    assert terms == ["PCR"]  # placeholders "A"/"B" never match


def test_detect_acronym_terms_matches_hyphenated_suffix():
    terms = detect_acronym_terms("Measure IL-6 levels after treatment.")
    assert terms == ["IL-6"]


@pytest.mark.parametrize("term", ["pcr", "PCR", "Pcr"])
def test_resolve_case_variants_all_match_same_entry(term):
    dictionary = load_acronym_dictionary()
    resolved, expansion = dictionary.resolve(term)
    assert resolved is True and expansion == "Polymerase Chain Reaction"


def test_detect_inline_definition_uses_matching_long_form_suffix():
    definitions = detect_inline_acronym_definitions(
        "Compare groups using standard deviation (SD), then report SD."
    )
    assert definitions == {"SD": ("standard deviation",)}


@pytest.mark.parametrize(
    "text",
    [
        "Report SD for each group.",
        "Use the SD signal.",
        "sample density (SDX)",
        "standard deviation (SD-1)",
    ],
)
def test_detect_inline_definition_rejects_unvalidated_forms(text):
    assert detect_inline_acronym_definitions(text) == {}


def test_detect_inline_definition_deduplicates_repeated_definitions():
    definitions = detect_inline_acronym_definitions(
        "standard deviation (SD) is reported; standard deviation (SD) is plotted."
    )
    assert definitions == {"SD": ("standard deviation",)}


def test_detect_inline_definition_retains_conflicting_expansions():
    definitions = detect_inline_acronym_definitions(
        "standard deviation (SD) differs from sudden death (SD)."
    )
    assert definitions == {"SD": ("standard deviation", "sudden death")}
