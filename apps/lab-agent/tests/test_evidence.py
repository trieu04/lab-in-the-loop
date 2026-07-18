"""Tests for the per-run evidence ledger: stable ids, bounded excerpts, and a
minimal durable audit summary (plan items 1 and 2)."""

from __future__ import annotations

import hashlib
import json

from lab_agent.evidence import AUDIT_BYTE_CAP, EXCERPT_MAX_CHARS, EvidenceLedger, compute_source_id


def test_source_id_is_deterministic_for_same_tool_arguments_and_content():
    id1 = compute_source_id("get_note", {"note_id": "n1"}, "hello world")
    id2 = compute_source_id("get_note", {"note_id": "n1"}, "hello world")
    assert id1 == id2


def test_source_id_is_order_independent_over_arguments():
    id1 = compute_source_id("get_note", {"a": 1, "b": 2}, "content")
    id2 = compute_source_id("get_note", {"b": 2, "a": 1}, "content")
    assert id1 == id2


def test_source_id_changes_with_tool_arguments_or_content():
    base = compute_source_id("get_note", {"note_id": "n1"}, "content")
    assert compute_source_id("get_widget", {"note_id": "n1"}, "content") != base
    assert compute_source_id("get_note", {"note_id": "n2"}, "content") != base
    assert compute_source_id("get_note", {"note_id": "n1"}, "other content") != base


def test_source_id_strips_namespacing_only_if_caller_passes_bare_name():
    """The ledger itself does not strip namespacing -- that is
    ``tool_bridge._bare_name``'s job before calling ``ledger.add``; a
    namespaced and bare tool name intentionally produce different ids here."""
    bare = compute_source_id("get_note", {}, "x")
    namespaced = compute_source_id("mcp__canvus__get_note", {}, "x")
    assert bare != namespaced


def test_ledger_add_is_idempotent_for_identical_calls():
    ledger = EvidenceLedger()
    id1 = ledger.add("get_note", {"note_id": "n1"}, "hello")
    id2 = ledger.add("get_note", {"note_id": "n1"}, "hello")
    assert id1 == id2
    assert len(ledger.audit_summary()) == 1  # not duplicated


def test_ledger_known_and_record_for_reflect_captured_reads():
    ledger = EvidenceLedger()
    sid = ledger.add("get_note", {"note_id": "n1"}, "hello")
    assert ledger.known(sid)
    assert not ledger.known("not-a-real-id")
    record = ledger.record_for(sid)
    assert record is not None
    assert record.tool == "get_note"
    assert record.excerpt == "hello"


def test_ledger_excerpt_is_bounded_even_for_huge_content():
    ledger = EvidenceLedger()
    huge = "x" * (EXCERPT_MAX_CHARS * 10)
    sid = ledger.add("download_pdf", {"url": "u"}, huge)
    record = ledger.record_for(sid)
    assert record is not None
    assert len(record.excerpt) == EXCERPT_MAX_CHARS


def test_ledger_max_records_caps_storage_but_ids_stay_deterministic():
    """Once the bound is hit, further distinct reads still get a
    reproducible id but are not stored -- so they correctly fail
    ``known``/``validate_citations`` rather than being silently trusted."""
    ledger = EvidenceLedger(max_records=2)
    id1 = ledger.add("get_note", {"note_id": "n1"}, "a")
    id2 = ledger.add("get_note", {"note_id": "n2"}, "b")
    id3 = ledger.add("get_note", {"note_id": "n3"}, "c")

    assert ledger.known(id1) and ledger.known(id2)
    assert not ledger.known(id3)  # dropped once the ledger is full
    assert id3 == compute_source_id("get_note", {"note_id": "n3"}, "c")  # id still deterministic


def test_validate_citations_requires_at_least_one_and_all_known():
    ledger = EvidenceLedger()
    sid = ledger.add("get_note", {"note_id": "n1"}, "hello")

    valid, known, unknown = ledger.validate_citations([sid])
    assert valid and known == [sid] and unknown == []

    valid, known, unknown = ledger.validate_citations([])
    assert not valid and known == [] and unknown == []  # zero citations is invalid

    valid, known, unknown = ledger.validate_citations(["fabricated-id"])
    assert not valid and known == [] and unknown == ["fabricated-id"]  # fully fabricated

    valid, known, unknown = ledger.validate_citations([sid, "fabricated-id"])
    assert not valid and known == [sid] and unknown == ["fabricated-id"]  # mixed is still invalid


def test_audit_summary_excludes_excerpt_arguments_and_url():
    """Durable audit rows carry only source_id/tool/content_hash -- never the
    excerpt, arguments, credentials, or a capability URL."""
    ledger = EvidenceLedger()
    secret = "https://lab.test/artifacts/abc?token=super-secret credential=xyz"
    sid = ledger.add("download_pdf", {"url": secret, "auth": "Bearer abc"}, secret)

    rows = ledger.audit_summary()
    expected_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    assert rows == [{"source_id": sid, "tool": "download_pdf", "content_hash": expected_hash}]
    blob = json.dumps(rows)
    assert "token" not in blob and "secret" not in blob and "Bearer" not in blob


def test_audit_summary_stays_under_byte_cap_with_many_records():
    ledger = EvidenceLedger(max_records=1000)
    for i in range(1000):
        ledger.add("get_note", {"note_id": f"n{i}"}, f"content {i}" * 50)

    rows = ledger.audit_summary()
    encoded = json.dumps(rows, separators=(",", ":")).encode("utf-8")
    assert len(encoded) <= AUDIT_BYTE_CAP
    assert len(rows) < 1000  # truncated well before exhausting all captured records
