"""Tests for the crash-safe outbox in ``lab_agent.recovery``.

``reconcile_or_execute`` must survive a restart at every boundary without
duplicating a non-idempotent canvas/provider write: before the intent is
persisted, after persistence but before the live probe, after the probe,
after execute, and after execute but before reconciliation. These tests
simulate each boundary by driving ``reconcile_or_execute`` directly against
a real on-disk ``StateStore`` -- no FakeMCP or other MCP-client coupling.
"""

from __future__ import annotations

import pytest

from lab_agent.recovery import ReconcileResult, idempotency_key, input_hash, reconcile_or_execute
from lab_agent.state_store import IntentHashMismatchError, StateStore

# ``store``/``clock`` come from tests/conftest.py (deterministic clock; the
# injected rng is unused here -- recovery has no backoff path).


def _counting_probe(calls: dict, *, returns: str | None):
    async def probe() -> str | None:
        calls["probe"] = calls.get("probe", 0) + 1
        return returns

    return probe


def _counting_executor(calls: dict, *, returns: str = "external-1"):
    async def execute() -> str:
        calls["exec"] = calls.get("exec", 0) + 1
        return returns

    return execute


def _failing_callable(calls: dict, key: str, error: Exception):
    async def fail():
        calls[key] = calls.get(key, 0) + 1
        raise error

    return fail


# ── idempotency_key / input_hash ────────────────────────────────────────


def test_idempotency_key_is_deterministic() -> None:
    a = idempotency_key("c1", "create_note", "t1/round1")
    b = idempotency_key("c1", "create_note", "t1/round1")
    assert a == b
    assert len(a) == 64


def test_idempotency_key_differs_by_any_component() -> None:
    base = idempotency_key("c1", "create_note", "t1")
    assert idempotency_key("c2", "create_note", "t1") != base
    assert idempotency_key("c1", "create_connector", "t1") != base
    assert idempotency_key("c1", "create_note", "t2") != base


def test_input_hash_is_deterministic_and_order_independent() -> None:
    a = input_hash({"x": 1, "y": 2})
    b = input_hash({"y": 2, "x": 1})
    assert a == b
    assert len(a) == 64


def test_input_hash_differs_by_value() -> None:
    assert input_hash({"x": 1}) != input_hash({"x": 2})


# ── reconcile_or_execute: happy path & fast paths ───────────────────────


@pytest.mark.asyncio
async def test_first_execution_persists_intent_and_executes(store: StateStore) -> None:
    calls: dict = {}
    result = await reconcile_or_execute(
        store.conn, clock=store.clock, canvas_id="c1", kind="create_note", key="k1",
        payload={"text": "hello"}, live_probe=_counting_probe(calls, returns=None),
        execute=_counting_executor(calls, returns="note-1"),
    )
    assert result == ReconcileResult(external_id="note-1", already_reconciled=False)
    assert calls == {"probe": 1, "exec": 1}

    intent = store.get_intent("k1")
    assert intent is not None
    assert intent.status.value == "reconciled"
    assert intent.external_id == "note-1"


@pytest.mark.asyncio
async def test_already_reconciled_intent_short_circuits_without_probe_or_execute(store: StateStore) -> None:
    calls: dict = {}
    payload = {"text": "hello"}
    await reconcile_or_execute(
        store.conn, clock=store.clock, canvas_id="c1", kind="create_note", key="k1",
        payload=payload, live_probe=_counting_probe(calls, returns=None),
        execute=_counting_executor(calls, returns="note-1"),
    )
    assert calls == {"probe": 1, "exec": 1}

    # Second call under the same key/payload must not touch probe or execute
    # again -- it is already durably reconciled.
    result = await reconcile_or_execute(
        store.conn, clock=store.clock, canvas_id="c1", kind="create_note", key="k1",
        payload=payload, live_probe=_counting_probe(calls, returns=None),
        execute=_counting_executor(calls, returns="note-1"),
    )
    assert result == ReconcileResult(external_id="note-1", already_reconciled=True)
    assert calls == {"probe": 1, "exec": 1}


@pytest.mark.asyncio
async def test_crash_after_effect_is_recovered_via_live_probe(store: StateStore) -> None:
    """Simulates: intent prepared, execute ran and returned an id, but the
    process crashed before ``mark_executed``/``mark_reconciled`` ran. On
    restart, the intent is still ``pending`` -- the live probe must find the
    already-existing effect and reconcile it without re-executing."""
    calls: dict = {}
    store.prepare_intent(idempotency_key="k1", canvas_id="c1", kind="create_note", input_hash=input_hash({"text": "hello"}))

    result = await reconcile_or_execute(
        store.conn, clock=store.clock, canvas_id="c1", kind="create_note", key="k1",
        payload={"text": "hello"}, live_probe=_counting_probe(calls, returns="note-existing"),
        execute=_counting_executor(calls),
    )
    assert result == ReconcileResult(external_id="note-existing", already_reconciled=True)
    assert calls == {"probe": 1}  # execute must never run -- the effect already exists

    intent = store.get_intent("k1")
    assert intent.status.value == "reconciled"
    assert intent.external_id == "note-existing"


# ── fail-closed duplicate-key detection ─────────────────────────────────


@pytest.mark.asyncio
async def test_hash_mismatch_under_same_key_fails_closed(store: StateStore) -> None:
    calls: dict = {}
    await reconcile_or_execute(
        store.conn, clock=store.clock, canvas_id="c1", kind="create_note", key="k1",
        payload={"text": "hello"}, live_probe=_counting_probe(calls, returns=None),
        execute=_counting_executor(calls, returns="note-1"),
    )

    with pytest.raises(IntentHashMismatchError):
        await reconcile_or_execute(
            store.conn, clock=store.clock, canvas_id="c1", kind="create_note", key="k1",
            payload={"text": "different"}, live_probe=_counting_probe(calls, returns=None),
            execute=_counting_executor(calls, returns="note-2"),
        )
    # The mismatch must be rejected before ever touching probe/execute again.
    assert calls == {"probe": 1, "exec": 1}


# ── probe / execute failure handling ────────────────────────────────────


@pytest.mark.asyncio
async def test_probe_failure_marks_intent_failed_and_reraises_without_claiming_completion(
    store: StateStore,
) -> None:
    calls: dict = {}
    probe_error = RuntimeError("mcp connection reset")
    with pytest.raises(RuntimeError, match="mcp connection reset"):
        await reconcile_or_execute(
            store.conn, clock=store.clock, canvas_id="c1", kind="create_note", key="k1",
            payload={"text": "hello"}, live_probe=_failing_callable(calls, "probe", probe_error),
            execute=_counting_executor(calls),
        )
    assert calls == {"probe": 1}
    assert "exec" not in calls  # execute must never run after a probe failure

    intent = store.get_intent("k1")
    assert intent.status.value == "failed"
    assert intent.last_error is not None and "mcp connection reset" in intent.last_error


@pytest.mark.asyncio
async def test_execute_failure_marks_intent_failed_and_reraises_without_claiming_completion(
    store: StateStore,
) -> None:
    calls: dict = {}
    exec_error = RuntimeError("provider timeout")
    with pytest.raises(RuntimeError, match="provider timeout"):
        await reconcile_or_execute(
            store.conn, clock=store.clock, canvas_id="c1", kind="create_note", key="k1",
            payload={"text": "hello"}, live_probe=_counting_probe(calls, returns=None),
            execute=_failing_callable(calls, "exec", exec_error),
        )
    assert calls == {"probe": 1, "exec": 1}

    intent = store.get_intent("k1")
    assert intent.status.value == "failed"
    assert intent.last_error == "provider timeout"


@pytest.mark.asyncio
async def test_retry_after_execute_failure_can_still_succeed(store: StateStore) -> None:
    """A caller retries the same key/payload after a transient execute
    failure -- prepare_intent must accept the retry (same input hash) and a
    subsequent successful execute must reconcile normally."""
    calls: dict = {}
    with pytest.raises(RuntimeError):
        await reconcile_or_execute(
            store.conn, clock=store.clock, canvas_id="c1", kind="create_note", key="k1",
            payload={"text": "hello"}, live_probe=_counting_probe(calls, returns=None),
            execute=_failing_callable(calls, "exec", RuntimeError("transient")),
        )

    result = await reconcile_or_execute(
        store.conn, clock=store.clock, canvas_id="c1", kind="create_note", key="k1",
        payload={"text": "hello"}, live_probe=_counting_probe(calls, returns=None),
        execute=_counting_executor(calls, returns="note-retry"),
    )
    assert result == ReconcileResult(external_id="note-retry", already_reconciled=False)
    assert store.get_intent("k1").status.value == "reconciled"
