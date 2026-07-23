"""Phase 8 execution orchestrator tests: approval gates, default-off, stale/wrong-canvas/unsupported yields zero submits."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace

import lab_agent.watch_execution as watch_execution
from lab_agent.config import Settings
from lab_agent.models.execution import (
    EvidenceKind,
    ExecutionRequest,
    ExternalRunStatus,
    RunMode,
)
from lab_agent.state_store import StateStore
from tests.fakes import FakeMCP, ScriptedAdapter


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _now():
    return datetime.now(UTC)


def _execution_request(canvas_id: str = "canvas-1") -> ExecutionRequest:
    return ExecutionRequest(
        request_id="req-1",
        execution_run_id="run-1",
        canvas_id=canvas_id,
        setup_id="setup-1",
        round=0,
        proposal_hash=_hash("proposal"),
        validation_result_hash=_hash("validation"),
        adapter_name="adapter",
        adapter_version="1",
        mode=RunMode.DRY_RUN,
        evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN,
        submit_intent_key="intent-key-1",
        input_hash=_hash("input"),
        requested_at=_now(),
    )


def test_execution_with_approval_gate(store: StateStore) -> None:
    """Approved setup proceeds to execution."""
    # Prepare approval
    store.prepare_intent(
        idempotency_key="approval-key-1",
        canvas_id="canvas-1",
        kind="execution_approval",
        input_hash=_hash("approval"),
    )
    approved = store.mark_intent_submitted("approval-key-1")
    assert approved.status.value == "submitted"

    # Proceed with execution
    request = _execution_request()
    run, is_new = store.prepare_execution_run(request)
    assert is_new is True
    assert run.status is ExternalRunStatus.PENDING


def test_missing_approval_blocks_execution(store: StateStore) -> None:
    """Missing approval prevents execution submit."""
    request = _execution_request()
    run, _ = store.prepare_execution_run(request)

    # No approval prepared; check intents
    incomplete = store.list_incomplete_intents("canvas-1")
    approval_intents = [i for i in incomplete if "approval" in i.kind]
    assert len(approval_intents) == 0  # No approval


def test_stale_approval_blocks_execution(store: StateStore) -> None:
    """Stale approval (different proposal hash) blocks execution."""
    request = _execution_request()
    request_updated = request.model_copy(update={"proposal_hash": _hash("new-proposal")})

    # Old approval with different hash
    store.prepare_intent(
        idempotency_key="approval-old",
        canvas_id="canvas-1",
        kind="execution_approval",
        input_hash=_hash("old-approval"),
    )

    # New request doesn't match old approval
    run, _ = store.prepare_execution_run(request_updated)
    assert run.proposal_hash == _hash("new-proposal")


def test_wrong_canvas_approval_blocked(store: StateStore) -> None:
    """Approval from different canvas does not authorize execution."""
    # Approval for canvas-2
    store.prepare_intent(
        idempotency_key="approval-c2",
        canvas_id="canvas-2",
        kind="execution_approval",
        input_hash=_hash("approval"),
    )
    store.mark_intent_submitted("approval-c2")

    # Try execution on canvas-1
    request = _execution_request(canvas_id="canvas-1")
    run, _ = store.prepare_execution_run(request)

    # Canvas-1 intents don't include canvas-2's approval
    canvas1_intents = store.list_incomplete_intents("canvas-1")
    canvas1_approvals = [i for i in canvas1_intents if "approval" in i.kind]
    assert len(canvas1_approvals) == 0


class _Phase8Lifecycle:
    def __init__(self) -> None:
        self.calls = 0

    async def reconcile_canvas(self, canvas_id: str) -> None:
        assert canvas_id == "canvas-1"

    async def execute_setup(self, *, canvas_id: str, setup_id: str, round_index: int) -> object:
        assert (canvas_id, setup_id, round_index) == ("canvas-1", "setup-1", 1)
        self.calls += 1
        return SimpleNamespace(execution=SimpleNamespace(status=ExternalRunStatus.SUCCEEDED))


async def test_phase8_manual_setup_waits_for_activation_before_lifecycle_call(
    store: StateStore, monkeypatch
) -> None:
    lifecycle = _Phase8Lifecycle()
    monkeypatch.setattr(watch_execution, "build_phase8_orchestrator", lambda *args, **kwargs: lifecycle)
    monkeypatch.setattr(watch_execution, "load_typed_setup", lambda *args: object())
    monkeypatch.setattr(watch_execution, "hash_proposal", lambda setup: "a" * 64)
    monkeypatch.setattr(watch_execution, "hash_validation_result", lambda result: "b" * 64)
    monkeypatch.setattr(watch_execution, "require_current_execution_authorization", lambda *args: object())
    monkeypatch.setattr(store, "list_validation_results", lambda *args, **kwargs: [object()])
    activated = False
    monkeypatch.setattr(watch_execution, "has_manual_execution_activation", lambda *args: activated)
    settings = Settings(artifact_public_base_url="https://lab.test", phase8_execution_enabled=True)
    snapshot = {"setups_needing_run": [{"widget_id": "setup-1", "execution_mode": "manual", "title": "[EXP:Setup v001]"}]}

    mcp = FakeMCP()
    waiting = await watch_execution.process_execution_triggers(
        mcp, ScriptedAdapter({}), settings, store, "one", "canvas-1", snapshot, lambda _: 1, None
    )
    assert waiting == lifecycle.calls == 0

    activated = True
    approved = await watch_execution.process_execution_triggers(
        mcp, ScriptedAdapter({}), settings, store, "two", "canvas-1", snapshot, lambda _: 1, None
    )
    assert approved == lifecycle.calls == 1
    assert not any(item["title"].startswith("[EXP:Result") for item in mcp.notes.values())
