"""Compatibility helpers for generated artifact key/hash migrations.

These keep durable Browser recovery stable across discriminator and intent-input
hash changes: old rows stay authoritative when found, while new writes use the
current keys.
"""

from __future__ import annotations

from collections.abc import Iterable

from lab_agent.artifact_composition import _compose_document
from lab_agent.artifact_store import ArtifactStore
from lab_agent.models.artifact import ArtifactDocument, ArtifactType
from lab_agent.recovery import idempotency_key, input_hash, legacy_idempotency_key
from lab_agent.state import artifact_idempotency, artifact_widgets
from lab_agent.state import artifacts as artifacts_repo
from lab_agent.state_store import IntentHashMismatchError, StateStore


def artifact_key(
    canvas_id: str, artifact_type: ArtifactType, discriminator: str, *, tenant_id: str = "default"
) -> str:
    """Return the canonical tenant-qualified artifact idempotency key."""
    return idempotency_key(canvas_id, f"artifact_{artifact_type.value}", discriminator, tenant_id=tenant_id)


def browser_key(
    canvas_id: str, artifact_type: ArtifactType, discriminator: str, *, tenant_id: str = "default"
) -> str:
    """Return the tenant-qualified Browser-widget idempotency key."""
    return idempotency_key(
        canvas_id, "create_browser", f"browser/{artifact_type.value}/{discriminator}", tenant_id=tenant_id
    )


def legacy_artifact_key(canvas_id: str, artifact_type: ArtifactType, discriminator: str) -> str:
    """Return a pre-P7b artifact key for read-only recovery probes."""
    return legacy_idempotency_key(canvas_id, f"artifact_{artifact_type.value}", discriminator)


def legacy_browser_key(canvas_id: str, artifact_type: ArtifactType, discriminator: str) -> str:
    """Return a pre-P7b Browser key for read-only recovery probes."""
    return legacy_idempotency_key(canvas_id, "create_browser", f"browser/{artifact_type.value}/{discriminator}")


def unique_keys(items: Iterable[str]) -> tuple[str, ...]:
    """Preserve order while dropping duplicate compatibility keys."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return tuple(out)


def _document_by_key(
    astore: ArtifactStore, *, canvas_id: str, key: str, artifact_type: ArtifactType
) -> ArtifactDocument | None:
    astore._require_canvas_scope(canvas_id)
    record = artifact_idempotency.get_artifact_by_idempotency_key(
        astore.conn, tenant_id=astore.tenant_id, canvas_id=canvas_id, idempotency_key=key
    )
    if record is None:
        return None
    if record.artifact_type != artifact_type.value:
        raise artifact_idempotency.ArtifactIdempotencyConflictError(
            f"idempotency_key {key!r} on canvas {canvas_id!r} is bound to "
            f"artifact_type {record.artifact_type!r}, not {artifact_type.value!r}"
        )
    version = artifacts_repo.get_version(
        astore.conn, tenant_id=astore.tenant_id, opaque_id=record.opaque_id, version=record.current_version
    )
    assert version is not None
    widget = artifact_widgets.get_mapping(
        astore.conn, tenant_id=astore.tenant_id, opaque_id=record.opaque_id
    )
    return _compose_document(record, version, widget_id=widget.widget_id if widget else None)


def find_existing_artifact(
    astore: ArtifactStore,
    *,
    canvas_id: str,
    artifact_type: ArtifactType,
    keys: Iterable[str],
    reuse_widget_id: str = "",
) -> ArtifactDocument | None:
    """Find the artifact to append before creating a new current-key row.

    ``reuse_widget_id`` lets an in-flight loop continue appending to the Browser
    widget it is already reading, which covers pre-upgrade round-scoped artifacts.
    ``keys`` carries the current key followed by known legacy keys for crash
    recovery before the Browser mapping exists.
    """
    if reuse_widget_id:
        doc = astore.get_artifact_by_widget(canvas_id=canvas_id, widget_id=reuse_widget_id)
        if doc is not None:
            if doc.artifact_type != artifact_type:
                raise artifact_idempotency.ArtifactIdempotencyConflictError(
                    f"widget {reuse_widget_id!r} is mapped to {doc.artifact_type.value!r}, "
                    f"not {artifact_type.value!r}"
                )
            return doc
    for key in unique_keys(keys):
        doc = _document_by_key(astore, canvas_id=canvas_id, key=key, artifact_type=artifact_type)
        if doc is not None:
            return doc
    return None


def legacy_browser_intent_hash(*, opaque_id: str, tagged_title: str, x: float, y: float) -> str:
    """Return the pre-upgrade Browser intent hash shape.

    The old payload excluded URL/token but included mutable label/position fields.
    Compatibility accepts exactly this hash for the same opaque artifact -- not any
    same-key hash -- so unrelated stale intents still fail closed.
    """
    return input_hash({"opaque_id": opaque_id, "title": tagged_title, "x": x, "y": y})


def prepare_browser_intent(
    store: StateStore,
    *,
    canvas_id: str,
    browser_key_value: str,
    opaque_id: str,
    compatible_hashes: Iterable[str] = (),
) -> None:
    """Prepare a Browser intent while accepting known pre-migration hashes only."""
    digest = input_hash({"opaque_id": opaque_id})
    existing = store.get_intent(browser_key_value, canvas_id=canvas_id)
    if existing is None:
        store.prepare_intent(
            idempotency_key=browser_key_value, canvas_id=canvas_id,
            kind="create_browser", input_hash=digest,
        )
        return
    if existing.canvas_id != canvas_id or existing.kind != "create_browser":
        raise RuntimeError(f"browser intent {browser_key_value!r} is bound to another mutation")
    if existing.input_hash == digest or existing.input_hash in set(compatible_hashes):
        store.prepare_intent(
            idempotency_key=browser_key_value, canvas_id=canvas_id,
            kind="create_browser", input_hash=existing.input_hash,
        )
        return
    raise IntentHashMismatchError(f"idempotency key {browser_key_value!r} already used with a different input")


__all__ = [
    "artifact_key",
    "browser_key",
    "find_existing_artifact", "legacy_artifact_key", "legacy_browser_key",
    "legacy_browser_intent_hash",
    "prepare_browser_intent",
    "unique_keys",
]
