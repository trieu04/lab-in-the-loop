"""Static-role authentication, authorization, and metadata-only audit support."""

from __future__ import annotations

import hmac
import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from functools import wraps
from typing import TYPE_CHECKING, Any, NoReturn, ParamSpec, TypeVar

from pydantic import SecretStr

from canvus_mcp.ingestion_validation import is_sha256

if TYPE_CHECKING:
    from canvus_mcp.ingestion_store import IngestionStore


class Role(StrEnum):
    """Fixed principals supported by the local MCP service."""

    READER = "reader"
    TRUSTED_SERVICE = "trusted_service"
    OPERATOR = "operator"


@dataclass(frozen=True)
class Principal:
    """Authenticated role without a token-derived identifier."""

    role: Role
    subject: str


class AccessDenied(PermissionError):  # noqa: N818
    """A deliberately non-specific authorization failure."""

    def __init__(self) -> None:
        super().__init__("access_denied")


_READER_ACTIONS = frozenset({"get_ingestion_status", "read_ingestion_chunks"})
_SERVICE_ACTIONS = frozenset(
    {"enqueue_ingestion", "create_note", "create_browser", "update_browser", "create_image", "create_connector"}
)
_OPERATOR_ACTIONS = frozenset({"retry_ingestion", "cancel_ingestion", "integrity", "admin"})
_AUDIT_ACTIONS = _READER_ACTIONS | _SERVICE_ACTIONS | _OPERATOR_ACTIONS
_AUDIT_REASONS = frozenset({"missing_or_invalid_credentials", "action_not_authorized", "canvas_not_authorized", "resource_not_available"})
_AUDIT_CANVAS = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
P = ParamSpec("P")
R = TypeVar("R")


class AccessPolicy:
    """Authenticates local static tokens and evaluates a fixed role matrix."""

    def __init__(
        self,
        *,
        store: IngestionStore,
        reader_token: SecretStr | None,
        trusted_service_token: SecretStr | None,
        operator_token: SecretStr | None,
        reader_canvases: tuple[str, ...],
        trusted_service_canvases: tuple[str, ...],
        operator_canvases: tuple[str, ...],
        stdio_role: Role,
        stdio_canvases: tuple[str, ...],
    ) -> None:
        self._store = store
        self._tokens = ((Role.READER, reader_token), (Role.TRUSTED_SERVICE, trusted_service_token), (Role.OPERATOR, operator_token))
        self._canvases = {Role.READER: reader_canvases, Role.TRUSTED_SERVICE: trusted_service_canvases, Role.OPERATOR: operator_canvases}
        self._stdio_role = stdio_role
        self._stdio_canvases = stdio_canvases

    def authenticate_bearer(self, token: str | None) -> Principal | None:
        """Return a role only after a constant-time configured-token match."""
        if not token:
            return None
        for role, configured in self._tokens:
            value = configured.get_secret_value() if configured is not None else ""
            if value and hmac.compare_digest(value, token):
                return Principal(role, "bearer")
        return None

    def principal_from_context(self, ctx: Any) -> Principal | None:
        """Read exactly one HTTP bearer header; stdio uses its configured role."""
        request = getattr(getattr(ctx, "request_context", None), "request", None)
        if request is None:
            return self.stdio_principal()
        values = _authorization_values(getattr(request, "headers", None))
        if len(values) != 1 or not values[0].startswith("Bearer "):
            return None
        token = values[0][len("Bearer "):]
        return self.authenticate_bearer(token) if token and " " not in token else None

    def stdio_principal(self) -> Principal:
        return Principal(self._stdio_role, "stdio")

    def allows(self, principal: Principal, *, action: str, canvas_id: str | None) -> bool:
        if action not in self._actions_for(principal.role):
            return False
        canvases = self._stdio_canvases if principal.subject == "stdio" else self._canvases[principal.role]
        return canvas_id is None or "*" in canvases or canvas_id in canvases

    def require(self, principal: Principal | None, *, action: str, canvas_id: str | None, job_id: int | None = None, asset_sha256: str | None = None) -> None:
        """Authorize before a write/network call, recording fixed denial metadata."""
        if principal is None:
            self._deny("anonymous", "anonymous", action, canvas_id, job_id, asset_sha256, "missing_or_invalid_credentials")
        if action not in self._actions_for(principal.role):
            self._deny(principal.subject, principal.role.value, action, canvas_id, job_id, asset_sha256, "action_not_authorized")
        if not self.allows(principal, action=action, canvas_id=canvas_id):
            self._deny(principal.subject, principal.role.value, action, canvas_id, job_id, asset_sha256, "canvas_not_authorized")

    def deny_resource(self, principal: Principal, *, action: str, canvas_id: str, job_id: int | None = None) -> NoReturn:
        """Hide cross-canvas and unknown job existence behind one denial shape."""
        self._deny(principal.subject, principal.role.value, action, canvas_id, job_id, None, "resource_not_available")

    def guarded(self, action: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
        """Decorate a tool before ``@mcp.tool()`` without exposing ``ctx`` schema."""
        def decorate(function: Callable[P, R]) -> Callable[P, R]:
            signature = inspect.signature(function)

            @wraps(function)
            async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                values = bound.arguments
                principal = self.principal_from_context(values.get("ctx"))
                self.require(principal, action=action, canvas_id=values.get("canvas_id"), job_id=values.get("job_id"))
                return await function(*args, **kwargs)  # type: ignore[misc]

            return wrapped  # type: ignore[return-value]
        return decorate

    @staticmethod
    def _actions_for(role: Role) -> frozenset[str]:
        if role is Role.READER:
            return _READER_ACTIONS
        if role is Role.TRUSTED_SERVICE:
            return _READER_ACTIONS | _SERVICE_ACTIONS
        return _READER_ACTIONS | _SERVICE_ACTIONS | _OPERATOR_ACTIONS

    def _deny(self, subject: str, role: str, action: str, canvas_id: str | None, job_id: int | None, asset_sha256: str | None, reason: str) -> NoReturn:
        safe_subject = subject if subject in {"anonymous", "bearer", "stdio"} else "unknown"
        safe_role = role if role in {"anonymous", *(item.value for item in Role)} else "unknown"
        safe_action = action if action in _AUDIT_ACTIONS else "unknown"
        safe_canvas = canvas_id if isinstance(canvas_id, str) and _AUDIT_CANVAS.fullmatch(canvas_id) else None
        safe_job = job_id if isinstance(job_id, int) and not isinstance(job_id, bool) and job_id >= 0 else None
        safe_hash = asset_sha256 if isinstance(asset_sha256, str) and is_sha256(asset_sha256) else None
        safe_reason = reason if reason in _AUDIT_REASONS else "unknown"
        self._store.conn.execute(
            "INSERT INTO authorization_audit(created_at, subject, category, role, action, canvas_id, job_id, asset_sha256, reason, decision) VALUES (?, ?, 'authorization', ?, ?, ?, ?, ?, ?, 'denied')",
            (self._store.clock(), safe_subject, safe_role, safe_action, safe_canvas, safe_job, safe_hash, safe_reason),
        )
        raise AccessDenied()


def _authorization_values(headers: Any) -> list[str]:
    """Return all Authorization values without normalizing malformed variants."""
    if headers is None:
        return []
    if hasattr(headers, "getlist"):
        return list(headers.getlist("authorization"))
    if isinstance(headers, list):
        return [value for key, value in headers if key.lower() == "authorization"]
    value = headers.get("authorization") if hasattr(headers, "get") else None
    return [] if value is None else [value]


__all__ = ["AccessDenied", "AccessPolicy", "Principal", "Role"]
