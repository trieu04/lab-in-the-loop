"""Contract tests for model instructions that affect deterministic setup gating."""

from lab_agent.prompts import SETUP_SYSTEM


def test_setup_prompt_prefers_self_contained_terms_before_human_review():
    prompt = SETUP_SYSTEM.lower()

    assert "expand" in prompt and "abbreviation" in prompt
    assert "operational" in prompt and "signal" in prompt
    assert "conservative" in prompt and "reversible" in prompt
    assert "safety" in prompt and "feasibility" in prompt
    assert "experimental design" in prompt and "result interpretation" in prompt
    assert "real source_id" in prompt
