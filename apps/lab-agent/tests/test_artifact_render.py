from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from lab_agent.artifact_render import render_artifact_html
from lab_agent.models.artifact import (
    ArtifactDocument,
    ArtifactMetadata,
    ArtifactProvenance,
    ArtifactType,
    ArtifactVersion,
)
from lab_agent.models.states import DecisionState

ASSETS = Path(__file__).parents[1] / "lab_agent" / "static"


def _document(
    payload: dict[str, Any] | None = None,
    *,
    metadata: ArtifactMetadata | None = None,
    provenance: ArtifactProvenance | None = None,
) -> ArtifactDocument:
    return ArtifactDocument(
        opaque_id="artifact-1",
        canvas_id="canvas-1",
        artifact_type=ArtifactType.RESULT,
        state=DecisionState.ANALYSIS_COMPLETE,
        round=3,
        current_version=2,
        payload=payload or {},
        metadata=metadata or ArtifactMetadata(),
        provenance=provenance
        or ArtifactProvenance(provider="claude", model_name="model", trigger_id="trigger"),
        content_hash="hash-2",
        widget_id="widget-1",
        created_at=10.0,
        updated_at=20.0,
    )


def _version(number: int, value: str) -> ArtifactVersion:
    return ArtifactVersion(
        opaque_id="artifact-1",
        version=number,
        payload={"result": value},
        metadata=ArtifactMetadata(tags=[value]),
        provenance=ArtifactProvenance(provider=value),
        content_hash=f"hash-{number}",
        created_at=float(number),
    )


def _tab_labels(rendered: str) -> list[str]:
    return re.findall(r'role="tab"[^>]*>([^<]+)</button>', rendered)


def _selected_tab_label(rendered: str) -> str | None:
    match = re.search(r'role="tab"[^>]*aria-selected="true"[^>]*>([^<]+)</button>', rendered)
    return match.group(1) if match else None


def test_single_experiment_renders_inline_without_tabs() -> None:
    """One experiment presents its content seamlessly inline -- sections are
    ``<h2>`` headings in one flow, never per-section tabs."""
    document = _document({"summary": "Measured result", "steps": ["run"]})

    first = render_artifact_html(document)
    second = render_artifact_html(document)

    assert first == second  # deterministic
    assert first.startswith("<!doctype html><html lang=\"en\">")
    assert _tab_labels(first) == []  # no tabs for a single experiment
    assert 'role="tablist"' not in first
    assert 'class="artifact-content"' in first  # one continuous content flow
    assert "<h2>Overview</h2>" in first  # summary grouped, rendered inline
    assert "<h2>Metadata</h2>" in first and "<h2>Audit</h2>" in first
    assert html.escape("Measured result", quote=True) in first
    assert '<link rel="stylesheet" href="/assets/artifact-view.css">' in first
    assert '<script defer src="/assets/artifact-tabs.js"></script>' in first


def test_multiple_experiments_render_as_tabs_with_latest_selected() -> None:
    """Two or more experiments split into one tab each (Exp001, Exp002, ...),
    ordered oldest..latest, and the latest is the default-selected tab."""
    payload = {"summary": "latest round"}
    versions = (_version(2, "new"), _version(1, "old"))

    rendered = render_artifact_html(_document(payload), versions)

    assert _tab_labels(rendered) == ["Exp001", "Exp002"]  # one tab per experiment
    assert 'role="tablist"' in rendered and 'aria-label="Experiments"' in rendered
    assert _selected_tab_label(rendered) == "Exp002"  # default tab is the latest
    assert rendered.index("panel-exp-1") < rendered.index("panel-exp-2")  # oldest..latest
    assert 'id="panel-exp-1" aria-labelledby="tab-exp-1" hidden' in rendered  # earlier hidden
    assert 'id="panel-exp-2" aria-labelledby="tab-exp-2">' in rendered  # latest visible
    assert "<noscript>" in rendered and "All experiments" in rendered

    # A single experiment collapses back to the inline (no-tabs) presentation.
    single = render_artifact_html(_document(payload), (_version(1, "only"),))
    assert _tab_labels(single) == []


def test_all_model_content_is_escaped_without_inline_execution() -> None:
    attacks = [
        '<script>alert("x&")</script>',
        '<svg/onload="alert(1)">',
        "<img src=x onerror='alert(2)'>",
        'quoted "value" & single \'value\'',
    ]
    metadata = ArtifactMetadata(tags=[attacks[2]], extra={attacks[1]: attacks[3]})
    provenance = ArtifactProvenance(
        provider=attacks[0],
        model_name=attacks[1],
        trigger_id=attacks[2],
        source_widget_id=attacks[3],
    )
    rendered = render_artifact_html(
        _document({"title": attacks[0], "payload": attacks[1:]}, metadata=metadata, provenance=provenance)
    )

    for attack in attacks:
        assert html.escape(attack, quote=True) in rendered
    assert "<script>alert" not in rendered
    assert "<svg" not in rendered
    assert "<img" not in rendered
    tags = "\n".join(re.findall(r"<[^>]+>", rendered))
    assert re.search(r"\son[a-z]+\s*=", tags, re.IGNORECASE) is None
    assert re.search(r"\sstyle\s*=", tags, re.IGNORECASE) is None
    assert "<style" not in rendered
    assert re.findall(r"<script\b[^>]*>.*?</script>", rendered, re.DOTALL) == [
        '<script defer src="/assets/artifact-tabs.js"></script>'
    ]
    assert "https://" not in rendered and "http://" not in rendered


def test_nested_and_large_payloads_are_preserved_in_sorted_safe_views() -> None:
    large = ("large <&> data \"quoted\"\n" * 2000).strip()
    document = _document(
        {
            "zeta": [{"second": "B", "first": "A"}],
            "alpha": {"empty": [], "body": large},
        }
    )

    rendered = render_artifact_html(document)

    assert html.escape(large, quote=True) in rendered
    assert rendered.index("<dt>alpha</dt>") < rendered.index("<dt>zeta</dt>")
    assert rendered.index("<dt>first</dt>") < rendered.index("<dt>second</dt>")
    assert "large &lt;&amp;&gt; data &quot;quoted&quot;" in rendered


def test_dependency_free_assets_cover_accessible_responsive_behavior() -> None:
    script = (ASSETS / "artifact-tabs.js").read_text(encoding="utf-8")
    styles = (ASSETS / "artifact-view.css").read_text(encoding="utf-8")

    for key in ("ArrowLeft", "ArrowRight", "Home", "End"):
        assert key in script
    for behavior in ("aria-selected", "tabIndex", ".hidden", "preventDefault", ".focus()"):
        assert behavior in script
    assert "fetch(" not in script and "innerHTML" not in script and "import " not in script
    for hook in (
        "focus-visible",
        "prefers-reduced-motion",
        "prefers-color-scheme",
        "overflow-x: auto",
        "min-height: 44px",
        "system-ui",
    ):
        assert hook in styles
    assert "@import" not in styles and "url(" not in styles
