"""Crash-safe canvas writes: intent-before-mutation, then reconcile via probe.

Each ``write_*_durable`` function persists a ``side_effect_intents`` row
*before* the canvas mutation (``recovery.reconcile_or_execute``, wrapped with
audit bookkeeping by ``intent_audit.reconcile_with_audit``), probes the live
canvas for a prior run's already-completed effect (``canvas_probe``), and
only creates the note/connector if the probe finds nothing. This is the
outbox pattern canvus-mcp's non-idempotent write tools require (see
``recovery.py`` module docstring).

Callers (``orchestrator_support.py``/``orchestrator.py``) build the literal
title/body strings themselves -- ``scripts/check-workflow-contract-parity.py``
greps those two files for the exact body fragments, so the f-strings must
stay physically there, not move into this module. This module only takes
already-built ``title``/``body`` and handles idempotency/probing/audit.

One loop attempt can own many round-scoped note/connector intents, so every
discriminator folds in the round and the relevant predecessor id (see each
function's docstring) -- never just ``kind`` alone.
"""

from __future__ import annotations

from lab_agent import canvas_probe, nodes
from lab_agent.intent_audit import connect_durable, reconcile_with_audit
from lab_agent.mcp_client import MCPClient
from lab_agent.models.states import DecisionState
from lab_agent.recovery import idempotency_key, legacy_idempotency_key
from lab_agent.state_store import StateStore

# Canvas layout: setups left, results right, closed between; one row per round.
_ROW = 420.0
_SETUP_X = 0.0
_RESULT_X = 520.0
_CLOSED_X = 260.0


async def write_setup_node_durable(
    mcp: MCPClient,
    store: StateStore,
    *,
    canvas_id: str,
    title: str,
    body: str,
    round_index: int,
    predecessor_id: str,
    edge_kind: str,
) -> str:
    """Create (or recover) the setup note, then draw its incoming connector.

    ``predecessor_id``/``edge_kind`` is the idea note (``"idea_setup"``) for a
    loop's first round, or the previous round's result note
    (``"result_setup"``) for a round-advance -- discriminated by
    ``predecessor_id`` so distinct predecessors under the same round never
    collide.
    """
    discriminator = f"setup/predecessor:{predecessor_id}/round:{round_index}"
    key = idempotency_key(canvas_id, "create_note_setup", discriminator, tenant_id=store.tenant_id)
    legacy_key = legacy_idempotency_key(canvas_id, "create_note_setup", discriminator)
    tagged_title = canvas_probe.tagged_title(title, key)
    x, y = _SETUP_X, round_index * _ROW

    async def probe() -> str | None:
        return await canvas_probe.probe_note_by_tag(mcp, canvas_id=canvas_id, bucket="setups", idempotency_key=key) or await canvas_probe.probe_note_by_tag(mcp, canvas_id=canvas_id, bucket="setups", idempotency_key=legacy_key)

    async def execute() -> str:
        return await nodes.create_node(mcp, canvas_id, tagged_title, body, x, y, state=DecisionState.RUNNING)

    setup_id = await reconcile_with_audit(
        mcp, store, canvas_id=canvas_id, kind="create_note_setup", discriminator=discriminator,
        payload={"title": title, "body": body, "x": x, "y": y}, round_index=round_index,
        live_probe=probe, execute=execute,
    )
    if setup_id and predecessor_id:
        await connect_durable(
            mcp, store, canvas_id=canvas_id, src_id=predecessor_id, dst_id=setup_id,
            edge_kind=edge_kind, round_index=round_index,
        )
    return setup_id


async def write_result_node_durable(
    mcp: MCPClient,
    store: StateStore,
    *,
    canvas_id: str,
    title: str,
    body: str,
    setup_id: str,
    robot_id: str,
    round_index: int,
) -> str:
    """Create (or recover) the result note, then draw its incoming robot connector.

    Discriminated by ``setup_id`` (not ``robot_id``) -- a robot reused across
    rounds must not collide with a prior round's result under the same key.
    """
    discriminator = f"result/setup:{setup_id}/round:{round_index}"
    key = idempotency_key(canvas_id, "create_note_result", discriminator, tenant_id=store.tenant_id)
    legacy_key = legacy_idempotency_key(canvas_id, "create_note_result", discriminator)
    tagged_title = canvas_probe.tagged_title(title, key)
    x, y = _RESULT_X, round_index * _ROW

    async def probe() -> str | None:
        return await canvas_probe.probe_note_by_tag(mcp, canvas_id=canvas_id, bucket="results", idempotency_key=key) or await canvas_probe.probe_note_by_tag(mcp, canvas_id=canvas_id, bucket="results", idempotency_key=legacy_key)

    async def execute() -> str:
        return await nodes.create_node(
            mcp, canvas_id, tagged_title, body, x, y, state=DecisionState.ANALYSIS_COMPLETE
        )

    result_id = await reconcile_with_audit(
        mcp, store, canvas_id=canvas_id, kind="create_note_result", discriminator=discriminator,
        payload={"title": title, "body": body, "x": x, "y": y, "robot_id": robot_id}, round_index=round_index,
        live_probe=probe, execute=execute,
    )
    if result_id and robot_id:
        await connect_durable(
            mcp, store, canvas_id=canvas_id, src_id=robot_id, dst_id=result_id,
            edge_kind="robot_result", round_index=round_index,
        )
    return result_id


async def write_closed_node_durable(
    mcp: MCPClient,
    store: StateStore,
    *,
    canvas_id: str,
    title: str,
    body: str,
    round_index: int,
    result_id: str,
) -> str:
    """Create (or recover) the terminal closed note, then link it from ``result_id``.

    Tagged and probed via ``scan_experiment_workflow``'s ``closeds`` bucket,
    the same connector-independent mechanism setup/result notes use (see
    ``canvas_probe`` module docstring) -- recovery does not depend on the
    ``result -> closed`` connector having been drawn yet.
    """
    discriminator = f"closed/result:{result_id}/round:{round_index}"
    key = idempotency_key(canvas_id, "create_note_closed", discriminator, tenant_id=store.tenant_id)
    legacy_key = legacy_idempotency_key(canvas_id, "create_note_closed", discriminator)
    tagged_title = canvas_probe.tagged_title(title, key)
    x, y = _CLOSED_X, (round_index + 1) * _ROW

    async def probe() -> str | None:
        return await canvas_probe.probe_note_by_tag(mcp, canvas_id=canvas_id, bucket="closeds", idempotency_key=key) or await canvas_probe.probe_note_by_tag(mcp, canvas_id=canvas_id, bucket="closeds", idempotency_key=legacy_key)

    async def execute() -> str:
        return await nodes.create_node(mcp, canvas_id, tagged_title, body, x, y, state=DecisionState.CLOSED)

    closed_id = await reconcile_with_audit(
        mcp, store, canvas_id=canvas_id, kind="create_note_closed", discriminator=discriminator,
        payload={"title": title, "body": body, "x": x, "y": y}, round_index=round_index,
        live_probe=probe, execute=execute,
    )
    if closed_id and result_id:
        await connect_durable(
            mcp, store, canvas_id=canvas_id, src_id=result_id, dst_id=closed_id,
            edge_kind="result_closed", round_index=round_index,
        )
    return closed_id


__all__ = ["write_closed_node_durable", "write_result_node_durable", "write_setup_node_durable"]
