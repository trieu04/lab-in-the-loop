"""Central authorization and metadata-only denial-audit tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from canvus_mcp.access_control import AccessDenied, AccessPolicy, Principal, Role
from canvus_mcp.ingestion_store import IngestionStore


class _Request:
    def __init__(self, headers: list[tuple[str, str]]) -> None:
        self.headers = headers


class _RequestContext:
    def __init__(self, request: _Request | None) -> None:
        self.request = request


class _Context:
    def __init__(self, request: _Request | None) -> None:
        self.request_context = _RequestContext(request)


def _policy(store: IngestionStore) -> AccessPolicy:
    return AccessPolicy(
        store=store,
        reader_token=SecretStr("reader-token"),
        trusted_service_token=SecretStr("service-token"),
        operator_token=SecretStr("operator-token"),
        reader_canvases=("reader-canvas",),
        trusted_service_canvases=("service-canvas",),
        operator_canvases=("*",),
        stdio_role=Role.READER,
        stdio_canvases=("stdio-canvas",),
    )


def test_bearer_authentication_uses_configured_static_roles(tmp_path: Path) -> None:
    store = IngestionStore(tmp_path / "ingestion.db")
    policy = _policy(store)
    try:
        assert policy.authenticate_bearer("reader-token") == Principal(Role.READER, "bearer")
        assert policy.authenticate_bearer("service-token") == Principal(Role.TRUSTED_SERVICE, "bearer")
        assert policy.authenticate_bearer("operator-token") == Principal(Role.OPERATOR, "bearer")
        assert policy.authenticate_bearer("wrong-token") is None
        assert policy.authenticate_bearer(None) is None
    finally:
        store.close()


def test_role_canvas_matrix_and_stdio_defaults_fail_closed(tmp_path: Path) -> None:
    store = IngestionStore(tmp_path / "ingestion.db")
    policy = _policy(store)
    try:
        reader = Principal(Role.READER, "bearer")
        service = Principal(Role.TRUSTED_SERVICE, "bearer")
        operator = Principal(Role.OPERATOR, "bearer")
        assert policy.allows(reader, action="read_ingestion_chunks", canvas_id="reader-canvas")
        assert not policy.allows(reader, action="enqueue_ingestion", canvas_id="reader-canvas")
        assert policy.allows(service, action="create_note", canvas_id="service-canvas")
        assert not policy.allows(service, action="retry_ingestion", canvas_id="service-canvas")
        assert policy.allows(operator, action="cancel_ingestion", canvas_id="any-canvas")
        assert policy.stdio_principal() == Principal(Role.READER, "stdio")
        assert policy.allows(policy.stdio_principal(), action="get_ingestion_status", canvas_id="stdio-canvas")
        assert not policy.allows(policy.stdio_principal(), action="get_ingestion_status", canvas_id="other")
    finally:
        store.close()


def test_context_uses_one_strict_bearer_header_and_stdio_is_explicit(tmp_path: Path) -> None:
    store = IngestionStore(tmp_path / "ingestion.db")
    policy = _policy(store)
    try:
        assert policy.principal_from_context(_Context(_Request([("Authorization", "Bearer reader-token")]))) == Principal(Role.READER, "bearer")
        assert policy.principal_from_context(_Context(_Request([("Authorization", "reader-token")]))) is None
        assert policy.principal_from_context(_Context(_Request([("Authorization", "Bearer reader-token"), ("Authorization", "Bearer reader-token")]))) is None
        assert policy.principal_from_context(_Context(None)) == Principal(Role.READER, "stdio")
    finally:
        store.close()


def test_denial_audit_drops_unbounded_or_invalid_identifiers(tmp_path: Path) -> None:
    store = IngestionStore(tmp_path / "ingestion.db")
    policy = _policy(store)
    leaked = "../../secret?token=" + "x" * 10_000
    try:
        with pytest.raises(AccessDenied):
            policy.require(None, action=leaked, canvas_id=leaked, job_id=-1, asset_sha256=leaked)
        row = store.conn.execute("SELECT subject, role, action, canvas_id, job_id, asset_sha256, reason FROM authorization_audit").fetchone()
        assert tuple(row) == ("anonymous", "anonymous", "unknown", None, None, None, "missing_or_invalid_credentials")
        assert store.conn.execute("SELECT length(canvas_id) FROM authorization_audit").fetchone()[0] is None
    finally:
        store.close()


def test_denial_audit_has_only_fixed_metadata(tmp_path: Path) -> None:
    store = IngestionStore(tmp_path / "ingestion.db")
    policy = _policy(store)
    try:
        with pytest.raises(AccessDenied, match="access_denied"):
            policy.require(
                None,
                action="enqueue_ingestion",
                canvas_id="restricted",
                job_id=4,
                asset_sha256="a" * 64,
            )
        row = store.conn.execute("SELECT * FROM authorization_audit").fetchone()
        assert row is not None
        assert row["role"] == "anonymous"
        assert row["action"] == "enqueue_ingestion"
        assert row["canvas_id"] == "restricted"
        assert row["job_id"] == 4
        assert row["asset_sha256"] == "a" * 64
        assert row["reason"] == "missing_or_invalid_credentials"
        assert "token" not in row.keys()
        assert "arguments" not in row.keys()
        assert "content" not in row.keys()
    finally:
        store.close()
