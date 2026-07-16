"""Pure topology checks for pending in-silico and human-review markers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from canvus_mcp.experiment_widgets import (
    ExpMarkers,
    _brief,
    _is_lab_lead_approval_marker,
    _is_scientist_review_marker,
    _is_setup,
    _is_validation,
)
from canvus_mcp.ragcluster import ConnectorIndex, _endpoint_id


def out_ids(index: ConnectorIndex, widget_id: str) -> list[str]:
    """Return widget ids directly connected from ``widget_id``."""
    return [_endpoint_id(index.connectors[c], "dst") for c in index.src_to_connectors.get(widget_id, [])]


def in_ids(index: ConnectorIndex, widget_id: str) -> list[str]:
    """Return widget ids directly connected to ``widget_id``."""
    return [_endpoint_id(index.connectors[c], "src") for c in index.dst_to_connectors.get(widget_id, [])]


def first_neighbor(
    index: ConnectorIndex, widget_ids: list[str], predicate: Callable[[Any], bool]
) -> str:
    """Return the first adjacent widget matching ``predicate``, if any."""
    for widget_id in widget_ids:
        widget = index.widgets_by_id.get(widget_id)
        if widget is not None and predicate(widget):
            return widget_id
    return ""


def scan_pending_gates(
    index: ConnectorIndex,
    markers: ExpMarkers,
    setups: list[str],
    validations: list[str],
    scientist_review_markers: list[str],
) -> dict[str, list[dict[str, str]]]:
    """Return non-authorizing, connector-backed workflow requests.

    A marker is canvas metadata only. This deliberately neither reads free text
    nor infers an actor, role, decision, or approval from marker presence.
    """
    setups_needing_validation: list[dict[str, str]] = []
    for setup_id in setups:
        validation_id = first_neighbor(index, out_ids(index, setup_id), lambda w: _is_validation(w, markers))
        if not validation_id:
            setups_needing_validation.append(_brief(index.widgets_by_id[setup_id]))

    validations_needing_scientist_review: list[dict[str, str]] = []
    for validation_id in validations:
        setup_id = first_neighbor(index, in_ids(index, validation_id), lambda w: _is_setup(w, markers))
        review_id = first_neighbor(index, out_ids(index, validation_id), lambda w: _is_scientist_review_marker(w, markers))
        if setup_id and not review_id:
            item = _brief(index.widgets_by_id[validation_id])
            item["setup_id"] = setup_id
            validations_needing_scientist_review.append(item)

    scientist_reviews_needing_lab_lead_approval: list[dict[str, str]] = []
    for review_id in scientist_review_markers:
        validation_id = first_neighbor(index, in_ids(index, review_id), lambda w: _is_validation(w, markers))
        setup_id = first_neighbor(index, in_ids(index, validation_id), lambda w: _is_setup(w, markers)) if validation_id else ""
        lab_lead_marker_id = first_neighbor(index, out_ids(index, review_id), lambda w: _is_lab_lead_approval_marker(w, markers))
        if validation_id and setup_id and not lab_lead_marker_id:
            item = _brief(index.widgets_by_id[review_id])
            item["setup_id"] = setup_id
            item["validation_id"] = validation_id
            scientist_reviews_needing_lab_lead_approval.append(item)

    return {
        "setups_needing_validation": setups_needing_validation,
        "validations_needing_scientist_review": validations_needing_scientist_review,
        "scientist_reviews_needing_lab_lead_approval": scientist_reviews_needing_lab_lead_approval,
    }


__all__ = ["first_neighbor", "in_ids", "out_ids", "scan_pending_gates"]
