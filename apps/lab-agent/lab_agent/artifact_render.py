"""Safe, deterministic HTML rendering for generated artifacts."""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from typing import Any

from lab_agent.models.artifact import ArtifactDocument, ArtifactVersion, TabKey

_LABELS = {
    TabKey.OVERVIEW: "Overview",
    TabKey.DETAILS: "Details",
    TabKey.EVIDENCE: "Evidence",
    TabKey.METADATA: "Metadata",
    TabKey.AUDIT: "Audit",
    TabKey.VALIDATION: "Validation",
    TabKey.EXECUTION: "Execution",
    TabKey.ANALYSIS: "Analysis",
    TabKey.VERSIONS: "Versions",
}
_BASELINE = (TabKey.OVERVIEW, TabKey.DETAILS, TabKey.EVIDENCE, TabKey.METADATA, TabKey.AUDIT)
_SECTION_KEYS = {
    TabKey.OVERVIEW: ("title", "summary", "rationale", "status", "decision", "reason"),
    TabKey.EVIDENCE: ("evidence", "evidence_refs", "sources", "references", "citations"),
    TabKey.VALIDATION: ("validation", "validations", "quality_flags", "approval", "approval_status"),
    TabKey.EXECUTION: (
        "execution",
        "inputs",
        "conditions",
        "steps",
        "parameters",
        "expected_readouts",
        "observations",
        "metrics",
    ),
    TabKey.ANALYSIS: (
        "analysis",
        "interpretation",
        "conclusion",
        "conclusions",
        "decision",
        "reason",
        "next_focus",
    ),
}
_EMPTY = '<p class="empty-state">No content available for this view.</p>'


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
        list_items = "".join(f"<li>{_render_value(item)}</li>" for item in value)
        return f'<ol class="data-list">{list_items}</ol>'
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


def _render_versions(versions: Sequence[ArtifactVersion]) -> str:
    ordered = sorted(versions, key=lambda item: item.version)
    rows = "".join(
        f"<tr><td>{_escape(item.version)}</td><td>{_escape(item.created_at)}</td>"
        f"<td>{_escape(item.content_hash)}</td></tr>"
        for item in ordered
    )
    table = (
        '<div class="table-scroll" tabindex="0" role="region" aria-label="Version summary">'
        "<table><thead><tr><th>Version</th><th>Created</th><th>Content hash</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )
    history = "".join(
        f"<details><summary>Version {_escape(item.version)}</summary>"
        f"{_render_value(item.model_dump(mode='python'))}</details>"
        for item in ordered
    )
    return f'{table}<div class="version-history">{history}</div>'


def _tabs(document: ArtifactDocument, versions: Sequence[ArtifactVersion]) -> tuple[TabKey, ...]:
    keys = list(_BASELINE)
    for key in (TabKey.VALIDATION, TabKey.EXECUTION, TabKey.ANALYSIS):
        if _selected(document.payload, _SECTION_KEYS[key]):
            keys.append(key)
    if versions:
        keys.append(TabKey.VERSIONS)
    return tuple(keys)


def _content(document: ArtifactDocument, key: TabKey, versions: Sequence[ArtifactVersion]) -> str:
    if key is TabKey.DETAILS:
        return _render_value(document.payload)
    if key is TabKey.METADATA:
        return _render_value(document.metadata.model_dump(mode="python"))
    if key is TabKey.AUDIT:
        return _render_value(_audit_data(document))
    if key is TabKey.VERSIONS:
        return _render_versions(versions)
    return _render_value(_selected(document.payload, _SECTION_KEYS[key]))


def render_artifact_html(
    document: ArtifactDocument, versions: Sequence[ArtifactVersion] = ()
) -> str:
    """Return one complete, escaped artifact document for the ASGI view route."""
    tabs = _tabs(document, versions)
    raw_title = document.payload.get("title")
    title = raw_title if isinstance(raw_title, str) and raw_title.strip() else (
        f"{document.artifact_type.value.replace('_', ' ').title()} artifact"
    )
    buttons = "".join(
        f'<button type="button" role="tab" id="tab-{key.value}" '
        f'aria-controls="panel-{key.value}" aria-selected="{str(index == 0).lower()}" '
        f'tabindex="{0 if index == 0 else -1}">{_LABELS[key]}</button>'
        for index, key in enumerate(tabs)
    )
    contents = {key: _content(document, key, versions) for key in tabs}
    panels = "".join(
        f'<section role="tabpanel" id="panel-{key.value}" aria-labelledby="tab-{key.value}"'
        f'{"" if index == 0 else " hidden"}><h2>{_LABELS[key]}</h2>{contents[key]}</section>'
        for index, key in enumerate(tabs)
    )
    fallback = "".join(
        f'<section class="fallback-panel"><h3>{_LABELS[key]}</h3>{contents[key]}</section>' for key in tabs
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
        f'<p class="artifact-context">Round {_escape(document.round)} · Version '
        f"{_escape(document.current_version)}</p></header>"
        f'<div class="tab-list" role="tablist" aria-label="Artifact views">{buttons}</div>'
        f'<div class="tab-panels">{panels}</div>'
        f'<noscript><section class="noscript-content"><h2>All artifact views</h2>{fallback}</section></noscript>'
        "</main></body></html>"
    )


__all__ = ["render_artifact_html"]
