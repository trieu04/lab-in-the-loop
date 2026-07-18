"""D1 regression at the orchestrator boundary: both the initial
setup-generation call path (``generate_setup``) and the next-round call path
(``run_loop``) must thread ``idea_text`` into ``evaluate_grounding``, not
only the freshly emitted setup.

``SETUP`` (from ``tests.fakes.grounded_setup()``) never mentions the acronym
used below, so if either call path failed to pass ``idea_text`` through, the
gate would wrongly report EXECUTABLE / would not stop the loop.
"""

from __future__ import annotations

import pytest

from lab_agent.config import Settings
from lab_agent.models.evidence import GroundingDecision
from lab_agent.orchestrator import generate_setup, run_loop
from lab_agent.state_store import StateStore
from tests.fakes import FakeMCP, ScriptedAdapter, grounded_setup

SETUP = grounded_setup()
IDEA_WITH_UNRESOLVED_ACRONYM = "{idea: optimize BIA response in assay}"

LOOP = {
    "loop_connector_id": "c5",
    "setup_id": "setup1",
    "result_id": "result1",
    "robot_id": "robot1",
    "idea_id": "idea1",
    "ragcluster_id": "rag1",
    "round": 1,
}


def _settings(**kw):
    return Settings(artifact_public_base_url="https://lab.test", **kw)  # type: ignore[call-arg]


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "state.db")
    yield s
    s.close()


async def test_generate_setup_needs_input_when_idea_has_unresolved_acronym_not_in_setup(store):
    mcp = FakeMCP(note_text={"idea1": IDEA_WITH_UNRESOLVED_ACRONYM})
    adapter = ScriptedAdapter({"ExperimentSetup": SETUP})

    outcome = await generate_setup(
        mcp, adapter, _settings(), store, canvas_id="c",
        idea_text=IDEA_WITH_UNRESOLVED_ACRONYM, idea_id="idea1",
        ragcluster_id="rag1", round_index=1,
    )

    assert outcome.decision is GroundingDecision.NEEDS_INPUT
    assert "BIA" in outcome.reason
    assert [w for w in mcp.notes.values() if w["title"].startswith("[EXP:Setup")] == []


async def test_loop_next_round_needs_input_when_idea_has_unresolved_acronym_not_in_setup(store):
    """``run_loop`` re-reads the same idea text for every round it grounds;
    an unresolved acronym confined to that idea (never quoted into the
    freshly emitted next-round setup) must still force NEEDS_INPUT instead
    of an executable next setup."""
    seed = {
        "idea1": IDEA_WITH_UNRESOLVED_ACRONYM,
        "setup1": "Round: 1\nmix A and B",
        "result1": "marker reduced",
    }
    mcp = FakeMCP(note_text=seed)
    adapter = ScriptedAdapter({
        "ExperimentSetup": SETUP,
        "ExperimentResult": {"summary": "A+B reduced marker 30%", "metrics": ["reduction=0.30"]},
        "LoopDecision": [{"proceed": True, "reason": "promising", "next_focus": "raise dose"}],
    })

    summary = await run_loop(mcp, adapter, _settings(), store, canvas_id="c", loop=dict(LOOP))

    assert summary.stopped_reason.startswith("needs_input:")
    assert "BIA" in summary.stopped_reason
    assert len(summary.setup_ids) == 1  # no next-round setup written
