"""Process-scoped tenant and canvas isolation primitives."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DOMAIN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)


class TenantConfigurationError(ValueError):
    """Tenant configuration is malformed or cannot share a process."""


class CanvasAccessDeniedError(PermissionError):
    """Requested canvas is outside the configured tenant scope."""


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Immutable tenant scope for one process credential domain."""

    tenant_id: str
    allowed_canvas_ids: frozenset[str] | set[str] | list[str] | tuple[str, ...]
    credential_domain: str

    def __post_init__(self) -> None:
        _require_identifier(self.tenant_id, "tenant_id")
        _require_domain(self.credential_domain)
        if isinstance(self.allowed_canvas_ids, str) or not isinstance(
            self.allowed_canvas_ids, (frozenset, set, list, tuple)
        ):
            raise TenantConfigurationError("allowed_canvas_ids must be a non-empty collection")
        canvas_ids = frozenset(self.allowed_canvas_ids)
        if not canvas_ids:
            raise TenantConfigurationError("allowed_canvas_ids must not be empty")
        for canvas_id in canvas_ids:
            _require_identifier(canvas_id, "allowed_canvas_ids")
        object.__setattr__(self, "allowed_canvas_ids", canvas_ids)

    def allows_canvas(self, canvas_id: object) -> bool:
        """Return whether a syntactically valid canvas id is exactly allowlisted."""

        return isinstance(canvas_id, str) and bool(_IDENTIFIER.fullmatch(canvas_id)) and canvas_id in self.allowed_canvas_ids

    def require_canvas(self, canvas_id: object) -> None:
        """Fail closed unless ``canvas_id`` belongs to this immutable scope."""

        if not self.allows_canvas(canvas_id):
            raise CanvasAccessDeniedError("canvas is outside configured tenant scope")


def require_single_credential_domain(contexts: Iterable[TenantContext]) -> str:
    """Validate that all process tenants use exactly one credential domain."""

    configured = tuple(contexts)
    if not configured:
        raise TenantConfigurationError("at least one tenant context is required")
    if not all(isinstance(context, TenantContext) for context in configured):
        raise TenantConfigurationError("process tenants must be TenantContext instances")
    domains = {context.credential_domain for context in configured}
    if len(domains) != 1:
        raise TenantConfigurationError("one credential domain is required per process")
    return next(iter(domains))


def _require_identifier(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise TenantConfigurationError(f"{field_name} must be a bounded identifier")


def _require_domain(value: object) -> None:
    if not isinstance(value, str) or not _DOMAIN.fullmatch(value):
        raise TenantConfigurationError("credential_domain must be a lowercase DNS domain")


__all__ = [
    "CanvasAccessDeniedError",
    "TenantConfigurationError",
    "TenantContext",
    "require_single_credential_domain",
]
