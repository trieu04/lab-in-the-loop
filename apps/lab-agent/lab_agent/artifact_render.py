"""Safe, deterministic HTML rendering for generated artifacts."""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from typing import Any

from lab_agent.models.artifact import (
    ArtifactDocument,
    ArtifactMetadata,
    ArtifactVersion,
    TabKey,
)

_LABELS = {
    TabKey.OVERVIEW: "Overview",
    TabKey.DETAILS: "Details",
    TabKey.EVIDENCE: "Evidence",
    TabKey.METADATA: "Metadata",
    TabKey.AUDIT: "Audit",
    TabKey.VALIDATION: "Validation",
    TabKey.EXECUTION: "Execution",
    TabKey.ANALYSIS: "Analysis",
}
_SECTION_KEYS = {
    TabKey.OVERVIEW: ("title", "summary", "rationale", "hypothesis", "status", "decision", "reason"),
    TabKey.EVIDENCE: (
        "evidence", "evidence_refs", "sources", "references", "citations",
        "evidence_status", "ambiguity_flags",
    ),
    TabKey.VALIDATION: (
        "validation", "validations", "quality_flags", "approval", "approval_history",
        "approval_status", "confidence",
    ),
    TabKey.EXECUTION: (
        "execution", "inputs", "conditions", "steps", "parameters",
        "expected_readouts", "observations", "metrics", "success_criteria", "constraints",
    ),
    TabKey.ANALYSIS: ("analysis", "interpretation", "conclusion", "conclusions", "next_focus"),
}
_EMPTY = '<p class="empty-state">No content available for this view.</p>'
_INTERNAL_PAYLOAD_KEYS = {"rendered"}


def _text(value: object) -> str:
    if value is None:
        return "Not provided"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _escape(value: object) -> str:
    return html.escape(_text(value), quote=True)


def _meaningful(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (Mapping, list, tuple)):
        return bool(value)
    return True


def _selected(payload: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    return {key: payload[key] for key in keys if key in payload and _meaningful(payload[key])}


def _render_value(value: object) -> str:
    if isinstance(value, Mapping):
        if not value:
            return _EMPTY
        items = []
        for key, item in sorted(value.items(), key=lambda pair: _text(pair[0])):
            items.append(f"<div><dt>{_escape(key)}</dt><dd>{_render_value(item)}</dd></div>")
        return f'<dl class="data-map">{"".join(items)}</dl>'
    if isinstance(value, (list, tuple)):
        if not value:
            return _EMPTY
        return f'<ol class="data-list">{"".join(f"<li>{_render_value(item)}</li>" for item in value)}</ol>'
    return f'<p class="data-value">{_escape(value)}</p>'


def _audit_data(document: ArtifactDocument) -> dict[str, object]:
    return {
        "artifact_type": document.artifact_type.value,
        "canvas_id": document.canvas_id,
        "content_hash": document.content_hash,
        "created_at": document.created_at,
        "current_version": document.current_version,
        "opaque_id": document.opaque_id,
        "provenance": document.provenance.model_dump(mode="python"),
        "round": document.round,
        "state": document.state.value,
        "updated_at": document.updated_at,
        "widget_id": document.widget_id,
    }


def _version_audit_data(document: ArtifactDocument, version: ArtifactVersion) -> dict[str, object]:
    return {
        "artifact_type": document.artifact_type.value,
        "canvas_id": document.canvas_id,
        "content_hash": version.content_hash,
        "created_at": version.created_at,
        "opaque_id": document.opaque_id,
        "provenance": version.provenance.model_dump(mode="python"),
        "round": version.payload.get("round", version.version),
        "state": document.state.value,
        "version": version.version,
        "widget_id": document.widget_id,
    }


def _payload_sections(payload: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    sections: list[tuple[str, dict[str, Any]]] = []
    used: set[str] = set()
    for key in (TabKey.OVERVIEW, TabKey.EVIDENCE, TabKey.VALIDATION, TabKey.EXECUTION, TabKey.ANALYSIS):
        data = _selected(payload, _SECTION_KEYS[key])
        if data:
            sections.append((_LABELS[key], data))
            used.update(data)
    details = {
        key: value for key, value in payload.items()
        if key not in used and key not in _INTERNAL_PAYLOAD_KEYS and _meaningful(value)
    }
    if details:
        insert_at = 1 if sections and sections[0][0] == _LABELS[TabKey.OVERVIEW] else 0
        sections.insert(insert_at, (_LABELS[TabKey.DETAILS], details))
    return sections


def _render_document_body(
    payload: Mapping[str, Any], metadata: ArtifactMetadata, audit: Mapping[str, Any]
) -> str:
    sections = _payload_sections(payload)
    sections.extend(((_LABELS[TabKey.METADATA], metadata.model_dump(mode="python")), (_LABELS[TabKey.AUDIT], dict(audit))))
    return "".join(
        f'<section class="artifact-section"><h2>{_escape(label)}</h2>{_render_value(data)}</section>'
        for label, data in sections
    ) or _EMPTY


def _round_number(payload: Mapping[str, Any], fallback: int) -> int:
    try:
        return int(payload.get("round") or fallback)
    except (TypeError, ValueError):
        return fallback


def _experiment_label(payload: Mapping[str, Any], fallback: int) -> str:
    return f"Exp{_round_number(payload, fallback):03d}"


def _version_tabs(document: ArtifactDocument, versions: Sequence[ArtifactVersion]) -> str:
    ordered = sorted(versions, key=lambda item: item.version)
    latest = len(ordered) - 1
    buttons = "".join(
        f'<button type="button" role="tab" id="tab-exp-{item.version}" '
        f'aria-controls="panel-exp-{item.version}" aria-selected="{str(index == latest).lower()}" '
        f'tabindex="{0 if index == latest else -1}">{_escape(_experiment_label(item.payload, item.version))}</button>'
        for index, item in enumerate(ordered)
    )
    panels = "".join(
        f'<section role="tabpanel" id="panel-exp-{item.version}" aria-labelledby="tab-exp-{item.version}"'
        f'{"" if index == latest else " hidden"}><h2>{_escape(_experiment_label(item.payload, item.version))}</h2>'
        f'{_render_document_body(item.payload, item.metadata, _version_audit_data(document, item))}</section>'
        for index, item in enumerate(ordered)
    )
    fallback = "".join(
        f'<section class="fallback-panel"><h3>{_escape(_experiment_label(item.payload, item.version))}</h3>'
        f'{_render_document_body(item.payload, item.metadata, _version_audit_data(document, item))}</section>'
        for item in ordered
    )
    return (
        f'<div class="tab-list" role="tablist" aria-label="Experiments">{buttons}</div>'
        f'<div class="tab-panels">{panels}</div>'
        f'<noscript><section class="noscript-content"><h2>All experiments</h2>{fallback}</section></noscript>'
    )


def render_artifact_html(document: ArtifactDocument, versions: Sequence[ArtifactVersion] = ()) -> str:
    """Return one complete, escaped artifact document for the ASGI view route."""
    raw_title = document.payload.get("title")
    title = raw_title if isinstance(raw_title, str) and raw_title.strip() else (
        f"{document.artifact_type.value.replace('_', ' ').title()} artifact"
    )
    body = _version_tabs(document, versions) if len(versions) > 1 else (
        f'<div class="artifact-content">{_render_document_body(document.payload, document.metadata, _audit_data(document))}</div>'
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="referrer" content="no-referrer">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'self'; script-src 'self'; img-src 'none'; font-src 'none'; "
        "connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'\">"
        f"<title>{_escape(title)}</title>"
        '<link rel="stylesheet" href="/assets/artifact-view.css">'
        '<script defer src="/assets/artifact-tabs.js"></script></head><body>'
        '<main class="artifact-shell"><header class="artifact-header">'
        f'<p class="eyebrow">{_escape(document.artifact_type.value.replace("_", " "))}</p>'
        f"<h1>{_escape(title)}</h1>"
        f'<p class="status">{_escape(document.state.value)}</p>'
        f'<p class="artifact-context">Round {_escape(document.round)} · Version {_escape(document.current_version)}</p>'
        f"</header>{body}</main></body></html>"
    )


__all__ = ["render_artifact_html"]
