"""Tests for the vendored RagCluster connection-graph logic."""

from __future__ import annotations

from canvus_mcp.ragcluster import (
    ConnectorIndex,
    connections_for_widget,
    is_ragcluster_widget,
)


def _img(wid: str, title: str) -> dict:
    return {"id": wid, "widget_type": "Image", "title": title}


def _note(wid: str, text: str = "") -> dict:
    return {"id": wid, "widget_type": "Note", "text": text}


def _pdf(wid: str) -> dict:
    return {"id": wid, "widget_type": "Pdf"}


def _conn(wid: str, src: str, dst: str) -> dict:
    return {"id": wid, "widget_type": "Connector", "src": {"id": src}, "dst": {"id": dst}}


def test_is_ragcluster_widget_matches_marker():
    assert is_ragcluster_widget(_img("i1", "RAGCluster_abc"))
    assert not is_ragcluster_widget(_img("i2", "just-an-image"))
    assert not is_ragcluster_widget(_note("n1"))


def test_is_ragcluster_widget_custom_marker():
    assert is_ragcluster_widget(_img("i1", "RAG_x"), marker="RAG_")
    # "RAGCluster_x" does not start with "RAG_" (underscore differs).
    assert not is_ragcluster_widget(_img("i2", "RAGCluster_x"), marker="RAG_")


def test_is_ragcluster_widget_name_fallback():
    # Title empty but 'name' extra carries the marker.
    assert is_ragcluster_widget({"id": "i", "widget_type": "Image", "name": "RAGCluster_z"})


def test_index_indexes_ragclusters_and_connectors():
    widgets = [
        _img("rag1", "RAGCluster_main"),
        _pdf("pdf1"),
        _note("note1", "{{ hello }}"),
        _conn("c1", "pdf1", "rag1"),
        _conn("c2", "note1", "rag1"),
    ]
    index = ConnectorIndex.build(widgets)
    assert index.ragcluster_ids == {"rag1": "RAGCluster_main"}
    assert set(index.dst_to_connectors["rag1"]) == {"c1", "c2"}
    assert index.src_to_connectors["pdf1"] == ["c1"]


def test_connections_for_ragcluster_reports_inputs():
    widgets = [
        _img("rag1", "RAGCluster_main"),
        _pdf("pdf1"),
        _note("note1"),
        _conn("c1", "pdf1", "rag1"),
        _conn("c2", "rag1", "note1"),
    ]
    index = ConnectorIndex.build(widgets)
    conns = connections_for_widget(index, "rag1")

    incoming = conns["incoming"]
    outgoing = conns["outgoing"]
    assert len(incoming) == 1
    assert incoming[0]["other"]["widget_type"] == "Pdf"
    assert incoming[0]["connector_id"] == "c1"
    assert len(outgoing) == 1
    assert outgoing[0]["other"]["widget_type"] == "Note"
    assert outgoing[0]["connector_id"] == "c2"


def test_connections_for_missing_widget_is_empty():
    index = ConnectorIndex.build([_img("rag1", "RAGCluster_x")])
    conns = connections_for_widget(index, "nope")
    assert conns == {"incoming": [], "outgoing": []}
