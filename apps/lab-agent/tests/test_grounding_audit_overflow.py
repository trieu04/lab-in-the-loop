"""D2 regression: a full evidence ledger plus a long needs-input reason must
not block the durable audit write or the needs-input artifact.

``EvidenceLedger.audit_summary()`` only bounds its own nested evidence rows;
the *enclosing* payload (those rows plus ``decision``/``reason``) is what
``StateStore.append_audit_event`` actually caps at
``lab_agent.state_store.MAX_PAYLOAD_BYTES``. Before this fix, a
retrieval-heavy ambiguous setup could raise ``AuditPayloadTooLargeError``
before ``write_needs_input_for_verdict`` ever ran -- see
``lab_agent.grounding.record_grounding_audit``.
"""

from __future__ import annotations

import json

import pytest

from lab_agent.config import Settings
from lab_agent.evidence import EvidenceLedger
from lab_agent.grounding import (
    GroundingVerdict,
    record_grounding_audit,
    write_needs_input_for_verdict,
)
from lab_agent.models.evidence import GroundingDecision
from lab_agent.state_store import MAX_PAYLOAD_BYTES, StateStore
from tests.fakes import FakeMCP


def _settings(**kw):
    return Settings(artifact_public_base_url="https://lab.test", **kw)  # type: ignore[call-arg]


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "state.db")
    yield s
    s.close()


def _full_ledger_and_long_reason() -> tuple[EvidenceLedger, str]:
    ledger = EvidenceLedger(max_records=1000)
    for i in range(1000):
        ledger.add("get_note", {"note_id": f"n{i}"}, f"content {i}")
    reason = "unresolved term(s): " + ", ".join(f"ABC{i}" for i in range(30))
    return ledger, reason


def test_full_ledger_and_long_reason_records_audit_under_store_cap(store):
    ledger, reason = _full_ledger_and_long_reason()
    verdict = GroundingVerdict(GroundingDecision.NEEDS_INPUT, reason)

    record_grounding_audit(store, "c", verdict, ledger)  # must not raise AuditPayloadTooLargeError

    events = [e for e in store.list_audit_events("c") if e.event == "grounding_evaluated"]
    assert len(events) == 1
    payload = events[0].payload
    assert payload["decision"] == "needs_input"
    assert payload["reason"] == reason[:200]
    payload_bytes = len(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    assert payload_bytes <= MAX_PAYLOAD_BYTES


async def test_full_ledger_and_long_reason_still_writes_one_needs_input_artifact(store):
    mcp = FakeMCP()
    mcp.seed_widget("setup1", "Browser", title="[EXP:Setup v001] A+B")
    ledger, reason = _full_ledger_and_long_reason()
    verdict = GroundingVerdict(GroundingDecision.NEEDS_INPUT, reason)
    record_grounding_audit(store, "c", verdict, ledger)  # audit succeeds before the write below

    first_id = await write_needs_input_for_verdict(
        mcp, store, _settings(), canvas_id="c", verdict=verdict, round_index=1,
        predecessor_id="setup1", edge_kind="setup_needs_input",
    )
    second_id = await write_needs_input_for_verdict(  # a repeated poll must dedup, not duplicate
        mcp, store, _settings(), canvas_id="c", verdict=verdict, round_index=1,
        predecessor_id="setup1", edge_kind="setup_needs_input",
    )

    assert first_id == second_id
    browsers = [w for w in mcp.notes.values() if w["widget_type"] == "Browser" and "Needs Input" in w["title"]]
    assert len(browsers) == 1
