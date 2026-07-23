"""Authenticated identity boundary for human approval decisions."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from pydantic import SecretStr

from lab_agent.models.validation import ApprovalRole, IdentityAssertion


class IdentityVerificationError(RuntimeError):
    """Base error for identity failures that must not create approval evidence."""


class IdentityAuthenticationError(IdentityVerificationError):
    """Credential did not authenticate an actor."""


class IdentityAuthorizationError(IdentityVerificationError):
    """Authenticated actor is not authorized for the requested role."""


class IdentityNotReadyError(IdentityVerificationError):
    """Production identity verification is unavailable or disabled."""


@runtime_checkable
class IdentityProvider(Protocol):
    """Verify a secret credential and return a bounded identity assertion."""

    async def verify(
        self,
        credential: SecretStr,
        required_role: ApprovalRole,
    ) -> IdentityAssertion:
        """Authenticate and authorize one approval actor or raise."""
        ...


@dataclass(frozen=True)
class DevelopmentIdentity:
    """Non-production actor keyed by a precomputed credential digest."""

    actor_id: str
    roles: frozenset[ApprovalRole]
    credential_hash: str


@dataclass(frozen=True)
class ProductionIdentityReadiness:
    """Configuration gates for a future production identity provider."""

    issuer_url: str | None = None
    audience: str | None = None
    credential_domain: str | None = None
    trust_configured: bool = False
    role_mapping_approved: bool = False

    @property
    def blockers(self) -> tuple[str, ...]:
        blockers: list[str] = []
        if not self.issuer_url or not self.issuer_url.startswith("https://"):
            blockers.append("https_issuer")
        if not self.audience:
            blockers.append("audience")
        if not self.credential_domain:
            blockers.append("credential_domain")
        if not self.trust_configured:
            blockers.append("trust_configuration")
        if not self.role_mapping_approved:
            blockers.append("role_mapping_approval")
        return tuple(blockers)

    @property
    def ready(self) -> bool:
        return not self.blockers


def hash_development_credential(credential: SecretStr | str) -> str:
    """Hash a development credential so providers never retain raw secrets."""

    raw = credential.get_secret_value() if isinstance(credential, SecretStr) else credential
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StaticDevelopmentIdentityProvider:
    """Explicitly non-production provider for local approval workflow tests."""

    identities: tuple[DevelopmentIdentity, ...]
    credential_domain: str = "development.local"
    provider_name: str = "static-development"
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    async def verify(
        self,
        credential: SecretStr,
        required_role: ApprovalRole,
    ) -> IdentityAssertion:
        candidate_hash = hash_development_credential(credential)
        identity = next(
            (
                item
                for item in self.identities
                if hmac.compare_digest(item.credential_hash, candidate_hash)
            ),
            None,
        )
        if identity is None:
            raise IdentityAuthenticationError("identity authentication failed")
        if required_role not in identity.roles:
            raise IdentityAuthorizationError("identity is not authorized for the requested role")
        return IdentityAssertion(
            actor_id=identity.actor_id,
            role=required_role,
            credential_domain=self.credential_domain,
            provider=self.provider_name,
            verified_at=self.clock(),
            production_eligible=False,
        )


@dataclass(frozen=True)
class DisabledProductionIdentityProvider:
    """Non-verifying placeholder that keeps production approval fail closed."""

    readiness: ProductionIdentityReadiness

    async def verify(
        self,
        credential: SecretStr,
        required_role: ApprovalRole,
    ) -> IdentityAssertion:
        del credential, required_role
        blockers = self.readiness.blockers
        if blockers:
            raise IdentityNotReadyError("production identity blocked: " + ",".join(blockers))
        raise IdentityNotReadyError("production identity implementation is not installed")


__all__ = [
    "DevelopmentIdentity",
    "DisabledProductionIdentityProvider",
    "IdentityAuthenticationError",
    "IdentityAuthorizationError",
    "IdentityNotReadyError",
    "IdentityProvider",
    "IdentityVerificationError",
    "ProductionIdentityReadiness",
    "StaticDevelopmentIdentityProvider",
    "hash_development_credential",
]
