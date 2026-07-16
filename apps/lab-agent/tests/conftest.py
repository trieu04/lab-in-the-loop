"""Shared fixtures for the durable-harness ledger tests.

A manually-advanced ``_Clock`` and a fixed ``_Rng`` make every lease expiry,
backoff jitter, and audit timestamp deterministic -- no real sleeps, no
skipped tests. Modules needing a different construction (e.g. the real-time
orchestrator integration) define a local ``store``/``clock`` fixture, which
shadows the one here.
"""

from __future__ import annotations

import pytest

from lab_agent.state_store import StateStore
from tests.fakes import grounded_setup

# Shared scenario fixtures for the durable-harness integration tests. A valid
# ``ExperimentSetup`` payload (grounded: sufficient evidence + a citation that
# resolves in the deterministic ledger the ``ScriptedAdapter`` populates, so it
# clears the Phase 4 grounding gate and reaches the Browser write path these
# tests exercise), a malformed one (missing the required ``rationale``), and a
# single idea-needing-setup workflow snapshot.
SETUP = grounded_setup()
MALFORMED_SETUP = {"steps": ["mix A and B"]}
IDEA_WORKFLOW = {
    "ideas_needing_setup": [{"widget_id": "idea1", "ragcluster_id": "rag1"}],
    "setups_needing_run": [],
    "loops": [],
}


class _Clock:
    """Deterministic, manually-advanced wall clock."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


class _Rng:
    """Deterministic uniform-random source; always returns the same fixed value."""

    def __init__(self, value: float = 0.5) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def rng() -> _Rng:
    return _Rng()


@pytest.fixture
def store(tmp_path, clock, rng):
    s = StateStore(tmp_path / "state.db", clock=clock, rng=rng)
    yield s
    s.close()
