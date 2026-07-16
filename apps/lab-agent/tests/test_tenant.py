"""Tests for process-scoped tenant and canvas isolation."""

from __future__ import annotations

import pytest

from lab_agent.tenant import (
    CanvasAccessDeniedError,
    TenantConfigurationError,
    TenantContext,
    require_single_credential_domain,
)


def test_context_freezes_exact_canvas_allowlist_and_guards_scope() -> None:
    context = TenantContext(
        tenant_id="tenant-a",
        allowed_canvas_ids=["canvas-a", "canvas-b"],
        credential_domain="research.example",
    )

    assert context.allowed_canvas_ids == frozenset({"canvas-a", "canvas-b"})
    assert context.allows_canvas("canvas-a")
    assert not context.allows_canvas("canvas-a-child")
    context.require_canvas("canvas-b")

    with pytest.raises(CanvasAccessDeniedError, match="configured tenant scope"):
        context.require_canvas("canvas-other")


def test_invalid_config_like_tenant_inputs_fail_closed() -> None:
    invalid_inputs = (
        {"tenant_id": "", "allowed_canvas_ids": {"canvas-a"}, "credential_domain": "research.example"},
        {"tenant_id": "tenant-a", "allowed_canvas_ids": set(), "credential_domain": "research.example"},
        {"tenant_id": "tenant-a", "allowed_canvas_ids": "canvas-a", "credential_domain": "research.example"},
        {"tenant_id": "tenant-a", "allowed_canvas_ids": {"canvas a"}, "credential_domain": "research.example"},
        {"tenant_id": "tenant-a", "allowed_canvas_ids": {"canvas-a"}, "credential_domain": "https://research.example"},
        {"tenant_id": "tenant-a", "allowed_canvas_ids": {"canvas-a"}, "credential_domain": "other domain"},
    )

    for values in invalid_inputs:
        with pytest.raises(TenantConfigurationError):
            TenantContext(**values)


def test_process_rejects_mixed_credential_domains() -> None:
    primary = TenantContext("tenant-a", {"canvas-a"}, "research.example")
    same_domain = TenantContext("tenant-b", {"canvas-b"}, "research.example")
    other_domain = TenantContext("tenant-c", {"canvas-c"}, "clinical.example")

    assert require_single_credential_domain([primary, same_domain]) == "research.example"
    with pytest.raises(TenantConfigurationError, match="one credential domain"):
        require_single_credential_domain([primary, other_domain])
    with pytest.raises(TenantConfigurationError, match="at least one"):
        require_single_credential_domain([])
