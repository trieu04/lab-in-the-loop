"""Mirror-first legacy-Note migration; dry-runs inspect and apply mirrors safely."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lab_agent import nodes
from lab_agent.artifact_migration_probe import (
    LegacyNote,
    MigrationShapeError,
    inspect_connections,
    scan_legacy_notes,
)
from lab_agent.artifact_migration_scope import require_migration_scope
from lab_agent.config import Settings
from lab_agent.durable_browser import (
    RENDERED_TEXT_KEY,
    ArtifactUrlError,
    require_public_base,
    write_artifact_browser_durable,
)
from lab_agent.intent_audit import connect_durable
from lab_agent.mcp_client import MCPClient
from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
from lab_agent.models.states import DecisionState
from lab_agent.state_store import StateStore

_STATE: dict[ArtifactType, DecisionState] = {
    ArtifactType.SETUP: DecisionState.RUNNING,
    ArtifactType.RESULT: DecisionState.ANALYSIS_COMPLETE,
    ArtifactType.CLOSED: DecisionState.CLOSED,
}


@dataclass
class ItemResult:
    """Per-note outcome; only ids/counts/status -- never a token or URL."""

    note_id: str
    artifact_type: str
    title: str
    status: str
    widget_id: str = ""
    inbound: int = 0
    outbound: int = 0
    connectors_mirrored: int = 0
    error: str = ""


@dataclass
class MigrationSummary:
    """Whole-run result the CLI renders as text or JSON (ids/counts/status only)."""

    canvas_id: str
    applied: bool
    items: list[ItemResult] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return sum(1 for item in self.items if item.status == "failed")


def build_import_payload(note: LegacyNote, text: str) -> dict[str, Any]:
    """Structured import payload: title, exact legacy text, source id, and the
    safely-derivable type/round -- no invented scientific fields."""
    payload: dict[str, Any] = {
        "title": note.title,
        "text": text,
        RENDERED_TEXT_KEY: text,
        "source_note_id": note.widget_id,
        "artifact_type": note.artifact_type.value,
        "imported": True,
    }
    if note.round:  # only when a vNNN marker made it safely derivable
        payload["round"] = note.round
    return payload


async def _mirror_connectors(
    mcp: MCPClient, store: StateStore, *, canvas_id: str, note_id: str,
    widget_id: str, inbound: list[str], outbound: list[str], round_index: int,
) -> int:
    """Clone each inbound/outbound edge onto the Browser mirror exactly once.

    ``connect_durable`` dedups by endpoints so repeats converge; self-loops and
    the note<->mirror pair are skipped so no edge is invented.
    """
    drawn = 0
    for src, dst, kind, others in (
        (None, widget_id, "migrated_in", inbound),
        (widget_id, None, "migrated_out", outbound),
    ):
        for other_id in others:
            if other_id in (note_id, widget_id):
                continue  # skip self-loop and the note<->mirror pair
            cid = await connect_durable(
                mcp, store, canvas_id=canvas_id, src_id=src or other_id,
                dst_id=dst or other_id, edge_kind=kind, round_index=round_index,
            )
            if cid:
                drawn += 1
    return drawn


async def mirror_one(
    mcp: MCPClient, store: StateStore, settings: Settings, *, canvas_id: str, note: LegacyNote
) -> ItemResult:
    """Mirror one legacy Note into a Browser artifact + equivalent connectors.

    Connections are inspected first (read-only): a malformed shape aborts the
    item before any write, so a failed item never leaves a half-drawn graph.
    """
    inbound, outbound = await inspect_connections(mcp, canvas_id, note.widget_id)
    text = await nodes.read_note_text(mcp, canvas_id, note.widget_id)
    provenance = ArtifactProvenance(
        provider="migration",
        trigger_id=f"migrate:{note.widget_id}",
        source_widget_id=note.widget_id,
    )
    widget_id = await write_artifact_browser_durable(
        mcp, store, settings, canvas_id=canvas_id, artifact_type=note.artifact_type,
        state=_STATE[note.artifact_type], title=note.title,
        payload=build_import_payload(note, text), provenance=provenance,
        discriminator=f"migrate/note:{note.widget_id}", round_index=note.round,
        predecessor_id="", edge_kind="", layout_anchor_id=note.widget_id,
    )
    drawn = await _mirror_connectors(
        mcp, store, canvas_id=canvas_id, note_id=note.widget_id, widget_id=widget_id,
        inbound=inbound, outbound=outbound, round_index=note.round,
    )
    return ItemResult(
        note_id=note.widget_id, artifact_type=note.artifact_type.value, title=note.title,
        status="mirrored", widget_id=widget_id, inbound=len(inbound),
        outbound=len(outbound), connectors_mirrored=drawn,
    )


async def _inventory_item(mcp: MCPClient, canvas_id: str, note: LegacyNote) -> ItemResult:
    """Read-only dry-run row: neighbor counts, or a visible shape error."""
    try:
        inbound, outbound = await inspect_connections(mcp, canvas_id, note.widget_id)
    except MigrationShapeError as exc:
        return ItemResult(note.widget_id, note.artifact_type.value, note.title, "shape_error", error=str(exc)[:200])
    return ItemResult(
        note.widget_id, note.artifact_type.value, note.title, "inventoried",
        inbound=len(inbound), outbound=len(outbound),
    )


async def run_migration(
    mcp: MCPClient,
    store: StateStore | None,
    settings: Settings,
    *,
    canvas_id: str,
    apply: bool,
) -> MigrationSummary:
    """Inventory (default) or mirror (``apply``) legacy generated Notes.

    Dry-run performs zero Browser/connector/DB writes. Apply fails closed on a
    missing/malformed base URL before any write, isolates each item's failure,
    and preserves already-completed idempotent items for a safe rerun.
    """
    require_migration_scope(settings, canvas_id, store=store if apply else None)
    if apply:
        require_public_base(settings.artifact_public_base_url)
        if store is None:
            raise ValueError("apply mode requires a durable StateStore")
    summary = MigrationSummary(canvas_id=canvas_id, applied=apply)
    for note in await scan_legacy_notes(mcp, canvas_id):
        if not apply:
            summary.items.append(await _inventory_item(mcp, canvas_id, note))
            continue
        assert store is not None
        try:
            summary.items.append(
                await mirror_one(mcp, store, settings, canvas_id=canvas_id, note=note)
            )
        except Exception as exc:  # noqa: BLE001 - isolate per-item failure; other items still run
            summary.items.append(
                ItemResult(
                    note.widget_id,
                    note.artifact_type.value,
                    note.title,
                    "failed",
                    error=f"{type(exc).__name__}: migration item failed",
                )
            )
    return summary


__all__ = [
    "ArtifactUrlError", "ItemResult", "LegacyNote", "MigrationShapeError", "MigrationSummary",
    "build_import_payload", "inspect_connections", "mirror_one", "require_public_base",
    "run_migration", "scan_legacy_notes",
]
