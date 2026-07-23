"""Crash-safe canonical-artifact Browser writes."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode, urlparse

from lab_agent import artifact_compat, artifact_layout, canvas_probe, nodes
from lab_agent.artifact_browser_registry import browser_bucket, fallback_layout
from lab_agent.artifact_store import ArtifactStore
from lab_agent.config import Settings
from lab_agent.intent_audit import connect_durable
from lab_agent.mcp_client import MCPClient
from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
from lab_agent.models.states import DecisionState
from lab_agent.state_store import StateStore

RENDERED_TEXT_KEY = "rendered"

class ArtifactUrlError(RuntimeError):
    """Browser writes need a valid public artifact URL."""


def require_public_base(base: str) -> None:
    parsed = urlparse(base)
    if not base or parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ArtifactUrlError("artifact_public_base_url is empty or malformed for Browser use")


def _capability_url(base: str, opaque_id: str, token: str) -> str:
    """Build ``{base}/artifacts/{opaque_id}?token=...`` safely (a bearer secret)."""
    return f"{base.rstrip('/')}/artifacts/{quote(opaque_id, safe='')}?{urlencode({'token': token})}"


def provenance_for(
    settings: Settings, *, source_widget_id: str, trigger_id: str = ""
) -> ArtifactProvenance:
    """Record the configured provider/model and truthful workflow lineage."""
    model = settings.anthropic_model if settings.model_provider == "claude" else settings.openai_model
    return ArtifactProvenance(
        provider=settings.model_provider,
        model_name=model,
        trigger_id=trigger_id,
        source_widget_id=source_widget_id,
    )


async def _reconcile_browser(
    mcp: MCPClient,
    store: StateStore,
    astore: ArtifactStore,
    *,
    canvas_id: str,
    browser_key: str,
    bucket: str,
    opaque_id: str,
    mapped_widget_id: str | None,
    tagged_title: str,
    url: str,
    x: float,
    y: float,
    round_index: int,
    legacy_browser_keys: tuple[str, ...] = (),
    legacy_intent_hashes: tuple[str, ...] = (),
) -> str:
    """Create or repair one Browser widget under its durable intent."""
    legacy_hash = artifact_compat.legacy_browser_intent_hash(opaque_id=opaque_id, tagged_title=tagged_title, x=x, y=y)
    artifact_compat.prepare_browser_intent(store, canvas_id=canvas_id, browser_key_value=browser_key, opaque_id=opaque_id, compatible_hashes=(legacy_hash, *legacy_intent_hashes))
    found_key = browser_key
    try:
        existing = mapped_widget_id
        if not existing:
            for key in artifact_compat.unique_keys((browser_key, *legacy_browser_keys)):
                existing = await canvas_probe.probe_browser_by_tag(mcp, canvas_id=canvas_id, bucket=bucket, idempotency_key=key)
                if existing:
                    found_key = key
                    break
        if existing:
            await nodes.update_artifact_widget(mcp, canvas_id, existing, url=url, title=tagged_title)
            widget_id, created = existing, False
        else:
            widget_id = await nodes.create_artifact_widget(mcp, canvas_id, tagged_title, url, x, y)
            created = True
        astore.map_widget(opaque_id, canvas_id=canvas_id, widget_id=widget_id)
    except Exception as exc:
        store.mark_intent_failed(browser_key, error=str(exc)[:200])
        store.append_audit_event(canvas_id, "intent_failed", {"kind": "create_browser", "key": browser_key[:16], "error": str(exc)[:200]}, round=round_index)
        raise
    if created:
        store.mark_intent_executed(browser_key, external_id=widget_id)
    store.mark_intent_reconciled(browser_key, external_id=widget_id)
    if found_key != browser_key and store.get_intent(found_key) is not None:
        store.mark_intent_reconciled(found_key, external_id=widget_id)
    store.append_audit_event(canvas_id, "intent_reconciled", {"kind": "create_browser", "key": browser_key[:16], "external_id": widget_id, "repaired": not created}, round=round_index)
    return widget_id


async def write_artifact_browser_durable(
    mcp: MCPClient,
    store: StateStore,
    settings: Settings,
    *,
    canvas_id: str,
    artifact_type: ArtifactType,
    state: DecisionState,
    title: str,
    payload: dict[str, Any],
    provenance: ArtifactProvenance,
    discriminator: str,
    round_index: int,
    predecessor_id: str,
    edge_kind: str,
    layout_anchor_id: str | None = None,
    legacy_discriminators: tuple[str, ...] = (),
    reuse_artifact_widget_id: str = "",
) -> str:
    """Persist a canonical artifact, render it as a Browser widget, and connect it."""
    base = settings.artifact_public_base_url
    require_public_base(base)  # fail closed before any token/MCP mutation
    astore = ArtifactStore(store.conn, clock=store.clock)
    artifact_key = artifact_compat.artifact_key(canvas_id, artifact_type, discriminator)
    legacy_artifact_keys = tuple(artifact_compat.artifact_key(canvas_id, artifact_type, legacy) for legacy in legacy_discriminators)
    doc = artifact_compat.find_existing_artifact(astore, canvas_id=canvas_id, artifact_type=artifact_type, keys=(artifact_key, *legacy_artifact_keys), reuse_widget_id=reuse_artifact_widget_id)
    if doc is None:
        doc = astore.get_or_create_artifact(canvas_id=canvas_id, idempotency_key=artifact_key, artifact_type=artifact_type, state=state, payload=payload, provenance=provenance, round=round_index)
    if round_index > doc.round or (
        artifact_type is ArtifactType.APPROVAL_STATUS and doc.payload != payload
    ):
        doc = astore.append_version(
            doc.opaque_id,
            canvas_id=canvas_id,
            state=state,
            payload=payload,
            provenance=provenance,
            round=round_index,
        )
    title = str(doc.payload.get("title") or title)
    token = astore.issue_token(doc.opaque_id, canvas_id=canvas_id)  # fresh per attempt
    url = _capability_url(base, doc.opaque_id, token)
    fallback = fallback_layout(artifact_type, round_index)
    x, y = await artifact_layout.resolve_artifact_position(mcp, canvas_id=canvas_id, anchor_widget_id=layout_anchor_id or predecessor_id, artifact_type=artifact_type, fallback=fallback)
    browser_key = artifact_compat.browser_key(canvas_id, artifact_type, discriminator)
    legacy_browser_keys = tuple(artifact_compat.browser_key(canvas_id, artifact_type, legacy) for legacy in legacy_discriminators)
    tagged = canvas_probe.tagged_title(title, browser_key)
    legacy_grid_hash = artifact_compat.legacy_browser_intent_hash(opaque_id=doc.opaque_id, tagged_title=tagged, x=fallback[0], y=fallback[1])
    widget_id = await _reconcile_browser(mcp, store, astore, canvas_id=canvas_id, browser_key=browser_key, bucket=browser_bucket(artifact_type), opaque_id=doc.opaque_id, mapped_widget_id=doc.widget_id, tagged_title=tagged, url=url, x=x, y=y, round_index=round_index, legacy_browser_keys=legacy_browser_keys, legacy_intent_hashes=(legacy_grid_hash,))
    if widget_id and predecessor_id:
        await connect_durable(mcp, store, canvas_id=canvas_id, src_id=predecessor_id, dst_id=widget_id, edge_kind=edge_kind, round_index=round_index)
    return widget_id


async def read_stage_text(mcp: MCPClient, store: StateStore, canvas_id: str, widget_id: str) -> str:
    """Return a setup/result stage's text for the model: the canonical artifact
    payload via the Browser widget mapping (never Browser HTML), falling back to
    :func:`lab_agent.nodes.read_note_text` for a legacy Note-based canvas."""
    astore = ArtifactStore(store.conn, clock=store.clock)
    doc = astore.get_artifact_by_widget(canvas_id=canvas_id, widget_id=widget_id)
    if doc is not None:
        rendered = doc.payload.get(RENDERED_TEXT_KEY)
        if isinstance(rendered, str) and rendered:
            return rendered
    return await nodes.read_note_text(mcp, canvas_id, widget_id)


__all__ = ["ArtifactUrlError", "RENDERED_TEXT_KEY", "provenance_for", "read_stage_text", "require_public_base", "write_artifact_browser_durable"]
