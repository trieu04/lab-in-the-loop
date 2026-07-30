"""Durable, tenant-qualified implementation for the demo seed operator command."""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING, Any

from lab_agent import canvas_probe, nodes
from lab_agent.intent_audit import connect_durable, reconcile_with_audit
from lab_agent.recovery import idempotency_key

if TYPE_CHECKING:
    from lab_agent.mcp_client import MCPClient
    from lab_agent.state_store import StateStore

_KEY = re.compile(r"^[A-Za-z0-9_.-]+$")
_IDEA_MARKERS = ("{idea:", "{idea+auto:")


class SeedError(RuntimeError):
    """A safe, operator-actionable seeding failure."""


def build_seed_title(args: Any, tenant_id: str) -> str:
    """Validate operator input and return a tenant-qualified idea title."""
    if not any(marker in args.idea_text for marker in _IDEA_MARKERS):
        raise SeedError("--idea-text must contain a supported {idea: or {idea+auto: marker")
    if not isinstance(args.idea_key, str) or not _KEY.fullmatch(args.idea_key):
        raise SeedError("--idea-key contains unsupported characters")
    base = args.title or f"[EXP:Idea] {args.idea_key}"
    if not isinstance(base, str) or not base.strip():
        raise SeedError("--title must not be blank")
    suffix = f" [tenant:{tenant_id}]"
    if len(base) + len(suffix) > 120:
        raise SeedError("--title is too long for tenant-qualified idempotency")
    return f"{base}{suffix}"


def _parse_result(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise SeedError("MCP returned an unavailable result") from exc
    if not isinstance(value, dict) or value.get("error"):
        raise SeedError("MCP returned an unavailable result")
    return value


def _existing_idea(snapshot: dict[str, Any], title: str, key: str) -> str | None:
    ideas = snapshot.get("ideas")
    if not isinstance(ideas, list):
        return None
    tag = f"#{canvas_probe.tag_suffix(key)}"
    ids = [
        item.get("widget_id") for item in ideas
        if isinstance(item, dict) and (item.get("title") == title or tag in item.get("title", ""))
    ]
    valid = [item for item in ids if isinstance(item, str) and item]
    if len(valid) > 1:
        raise SeedError("multiple ideas use the requested idempotency title")
    return valid[0] if valid else None


def _incoming_from(report: dict[str, Any]) -> set[str]:
    incoming = report.get("incoming")
    if not isinstance(incoming, list):
        return set()
    return {
        other["widget_id"] for item in incoming
        if isinstance(item, dict) and isinstance((other := item.get("other")), dict)
        and isinstance(other.get("widget_id"), str)
    }


def _result(canvas_id: str, action: str, applied: bool, idea_id: str | None) -> dict[str, Any]:
    return {"canvas_id": canvas_id, "action": action, "applied": applied, "idea_widget_id": idea_id}


async def seed_demo(
    mcp: MCPClient, store: StateStore | None, args: Any, *, tenant_id: str, title: str
) -> dict[str, Any]:
    """Inspect a RagCluster and, under ``--apply``, durably seed one idea."""
    if args.apply and store is None:
        raise SeedError("apply requires a durable runtime")
    if store is not None and store.tenant_id != tenant_id:
        raise SeedError("durable runtime tenant does not match configured scope")
    key = idempotency_key(
        args.canvas, "create_note_demo_seed",
        f"cluster:{args.ragcluster_widget_id}/idea:{args.idea_key}", tenant_id=tenant_id,
    )
    cluster_result, snapshot_result = await asyncio.gather(
        mcp.call_tool("check_ragcluster_connections", {
            "canvas_id": args.canvas, "ragcluster_widget_id": args.ragcluster_widget_id,
        }),
        mcp.call_tool("scan_experiment_workflow", {"canvas_id": args.canvas}),
        return_exceptions=True,
    )
    if isinstance(cluster_result, BaseException):
        raise cluster_result
    cluster = _parse_result(cluster_result)
    clusters = cluster.get("clusters")
    found = isinstance(clusters, list) and any(
        isinstance(item, dict) and item.get("widget_id") == args.ragcluster_widget_id
        and item.get("found") is True and item.get("is_ragcluster") is True for item in clusters
    )
    if not found:
        raise SeedError("the requested RagCluster widget was not found")
    if isinstance(snapshot_result, BaseException):
        raise snapshot_result
    snapshot = _parse_result(snapshot_result)
    idea_id = _existing_idea(snapshot, title, key)
    action = "create_note_and_connector"
    if idea_id is not None:
        report = _parse_result(await mcp.call_tool("check_widget_connections", {
            "canvas_id": args.canvas, "widget_id": idea_id,
        }))
        incoming = _incoming_from(report)
        if args.ragcluster_widget_id in incoming:
            return _result(args.canvas, "already_seeded", False, idea_id)
        if incoming:
            raise SeedError("an idea with this title is connected to another RagCluster")
        action = "connect_existing_note"
    if not args.apply:
        return _result(args.canvas, f"would_{action}", False, idea_id)
    assert store is not None  # apply mode requires the durable runtime above.
    if idea_id is None:
        tagged_title = canvas_probe.tagged_title(title, key)

        async def probe() -> str | None:
            return await canvas_probe.probe_note_by_tag(
                mcp, canvas_id=args.canvas, bucket="ideas", idempotency_key=key
            )

        async def execute() -> str:
            return await nodes.create_node(
                mcp, args.canvas, tagged_title, args.idea_text, args.x, args.y
            )

        idea_id = await reconcile_with_audit(
            mcp, store, canvas_id=args.canvas, kind="create_note_demo_seed",
            discriminator=f"cluster:{args.ragcluster_widget_id}/idea:{args.idea_key}",
            payload={"title": title, "idea_key": args.idea_key, "cluster": args.ragcluster_widget_id},
            round_index=0, live_probe=probe, execute=execute,
        )
    await connect_durable(
        mcp, store, canvas_id=args.canvas, src_id=args.ragcluster_widget_id,
        dst_id=idea_id, edge_kind="demo_seed", round_index=0,
    )
    return _result(args.canvas, action, True, idea_id)


__all__ = ["SeedError", "build_seed_title", "seed_demo"]
