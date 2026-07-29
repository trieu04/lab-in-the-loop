"""Tenant boundary validation shared by artifact migration entry points."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lab_agent.runtime import RuntimeStartupError, resolve_tenant_context

if TYPE_CHECKING:
    from lab_agent.config import Settings
    from lab_agent.state_store import StateStore
    from lab_agent.tenant import TenantContext


def require_migration_scope(
    settings: Settings, canvas_id: str, *, store: StateStore | None = None
) -> TenantContext | None:
    """Resolve immutable runtime scope and reject an unauthorized canvas first."""
    context = resolve_tenant_context(settings)
    if context is not None:
        context.require_canvas(canvas_id)
    if store is not None and store.tenant_context != context:
        raise RuntimeStartupError("migration store scope does not match settings")
    return context


__all__ = ["require_migration_scope"]
