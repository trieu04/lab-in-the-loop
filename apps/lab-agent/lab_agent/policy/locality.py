"""Data-locality authorization (NFR-LITL-002, fail closed).

Runs *before* any provider receives content: given the classifications present
in the material a call would send, it decides which providers may see it. The
policy is default-deny -- a provider not listed for a classification (or a
provider with no entry at all) receives nothing, and an ``UNKNOWN`` (unclassified)
source is treated as sensitive, never waved through.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from lab_agent.models.governance import Authorization, DataClassification
from lab_agent.provider_endpoints import is_valid_provider_endpoint


class LocalityPolicy:
    """Provider -> approved-classification allowlist with fail-closed checks."""

    def __init__(
        self, provider_classifications: dict[str, list[str]], provider_endpoints: dict[str, str]
    ) -> None:
        self._map: dict[str, frozenset[DataClassification]] = {
            provider: frozenset(DataClassification(c) for c in classes)
            for provider, classes in provider_classifications.items()
        }
        self._endpoints = {
            provider: endpoint
            for provider, endpoint in provider_endpoints.items()
            if is_valid_provider_endpoint(endpoint, provider=provider)
        }

    def authorize(
        self, provider: str, classifications: Iterable[DataClassification]
    ) -> Authorization:
        """Authorize sending content of these classifications to ``provider``.

        An unconfigured provider is denied outright. Any single classification
        the provider is not approved for denies the whole call (the most
        restrictive class governs). Empty classifications are treated as
        :attr:`DataClassification.INTERNAL` -- system-generated lab content is
        never assumed public.
        """
        endpoint = self._endpoints.get(provider)
        if endpoint is None:
            return Authorization(False, f"provider {provider!r} has no approved endpoint")
        approved = self._map.get(provider)
        if approved is None:
            return Authorization(False, f"provider {provider!r} not in locality allowlist")
        needed = set(classifications) or {DataClassification.INTERNAL}
        if DataClassification.UNKNOWN in needed:
            return Authorization(False, "unknown content classification is always denied")
        for classification in needed:
            if classification not in approved:
                return Authorization(
                    False,
                    f"provider {provider!r} not approved for {classification.value!r} content",
                )
        return Authorization(True, f"authorized:{endpoint}")

    def allowed_providers(
        self, providers: Sequence[str], classifications: Iterable[DataClassification]
    ) -> list[str]:
        """The subset of ``providers`` authorized for these classifications,
        preserving order so routing preference is respected."""
        needed = list(classifications)
        return [p for p in providers if self.authorize(p, needed).allowed]


__all__ = ["LocalityPolicy"]
