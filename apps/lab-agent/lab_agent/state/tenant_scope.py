"""Tenant-bound access guard shared by durable state-store facades."""

from __future__ import annotations

from lab_agent.tenant import TenantContext

_DEFAULT_TENANT_ID = "default"


class TenantScopeStoreMixin:
    """Bind a store to one tenant and fail closed for unallowlisted canvases."""

    tenant_context: TenantContext | None
    tenant_id: str

    def _configure_tenant_scope(self, tenant_context: TenantContext | None) -> None:
        configured = getattr(self, "tenant_context", None)
        if configured is not None and configured != tenant_context:
            raise RuntimeError("state-store tenant scope is immutable")
        self.tenant_context = tenant_context
        self.tenant_id = (
            tenant_context.tenant_id if tenant_context is not None else _DEFAULT_TENANT_ID
        )

    def _require_canvas_scope(self, canvas_id: str) -> None:
        if self.tenant_context is not None:
            self.tenant_context.require_canvas(canvas_id)
        elif not isinstance(canvas_id, str) or not canvas_id:
            raise ValueError("canvas_id must be non-empty")

    def _allowed_canvas_ids(self) -> tuple[str, ...] | None:
        """Return immutable scope for SQL filtering, if this store is bound."""
        if self.tenant_context is None:
            return None
        return tuple(sorted(self.tenant_context.allowed_canvas_ids))


__all__ = ["TenantScopeStoreMixin"]
