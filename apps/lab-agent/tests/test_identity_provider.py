"""Security contract tests for the Phase 7 identity-provider boundary."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from lab_agent.integrations.identity import (
    DevelopmentIdentity,
    DisabledProductionIdentityProvider,
    IdentityAuthenticationError,
    IdentityAuthorizationError,
    IdentityNotReadyError,
    IdentityProvider,
    ProductionIdentityReadiness,
    StaticDevelopmentIdentityProvider,
    hash_development_credential,
)
from lab_agent.models.validation import ApprovalRole

NOW = datetime(2026, 7, 23, 9, 0, tzinfo=UTC)
SECRET = "local-development-secret"


def _provider() -> StaticDevelopmentIdentityProvider:
    return StaticDevelopmentIdentityProvider(
        identities=(
            DevelopmentIdentity(
                actor_id="scientist-1",
                roles=frozenset({ApprovalRole.SCIENTIST}),
                credential_hash=hash_development_credential(SECRET),
            ),
        ),
        clock=lambda: NOW,
    )


async def test_static_provider_verifies_role_without_retaining_raw_credential() -> None:
    provider = _provider()

    assertion = await provider.verify(SecretStr(SECRET), ApprovalRole.SCIENTIST)

    assert isinstance(provider, IdentityProvider)
    assert assertion.actor_id == "scientist-1"
    assert assertion.role is ApprovalRole.SCIENTIST
    assert assertion.authenticated is True
    assert assertion.production_eligible is False
    assert assertion.credential_domain == "development.local"
    assert SECRET not in repr(provider)


async def test_static_provider_rejects_unknown_credential_without_echoing_it() -> None:
    unknown = "unknown-secret-value"

    with pytest.raises(IdentityAuthenticationError) as exc_info:
        await _provider().verify(SecretStr(unknown), ApprovalRole.SCIENTIST)

    assert str(exc_info.value) == "identity authentication failed"
    assert unknown not in str(exc_info.value)


async def test_static_provider_rejects_wrong_role() -> None:
    with pytest.raises(IdentityAuthorizationError, match="not authorized"):
        await _provider().verify(SecretStr(SECRET), ApprovalRole.LAB_LEAD)


async def test_disabled_production_provider_reports_readiness_blockers() -> None:
    provider = DisabledProductionIdentityProvider(
        ProductionIdentityReadiness(issuer_url="http://unsafe.example")
    )

    assert provider.readiness.blockers == (
        "https_issuer",
        "audience",
        "credential_domain",
        "trust_configuration",
        "role_mapping_approval",
    )
    with pytest.raises(IdentityNotReadyError, match="https_issuer"):
        await provider.verify(SecretStr(SECRET), ApprovalRole.SCIENTIST)


async def test_production_identity_remains_disabled_when_configuration_is_ready() -> None:
    provider = DisabledProductionIdentityProvider(
        ProductionIdentityReadiness(
            issuer_url="https://identity.example",
            audience="lab-agent",
            credential_domain="research.example",
            trust_configured=True,
            role_mapping_approved=True,
        )
    )

    assert provider.readiness.ready
    with pytest.raises(IdentityNotReadyError, match="implementation is not installed"):
        await provider.verify(SecretStr(SECRET), ApprovalRole.SCIENTIST)
