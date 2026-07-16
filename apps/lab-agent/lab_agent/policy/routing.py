"""Deterministic, config-backed task-stage routing (NFR-LITL-003).

The orchestration code never names a provider; it names a :class:`TaskStage`
and asks the :class:`RoutingTable` which configured, locality-authorized
provider serves it. The table is a plain ``stage -> ordered preference`` map, so
provider selection is pure data with no dynamic plugin surface (the deferred
gateway trigger in the phase plan).
"""

from __future__ import annotations

from collections.abc import Sequence

from lab_agent.models.governance import RoutingDecision, TaskStage


class NoEligibleProviderError(RuntimeError):
    """Raised when no preferred provider for a stage is both configured and
    locality-authorized. Fail closed -- never fall through to a denied provider."""


class RoutingTable:
    """Maps a stage to an ordered provider preference and resolves the winner."""

    def __init__(
        self,
        table: dict[str, list[str]],
        *,
        default_provider: str,
        provider_models: dict[str, str],
    ) -> None:
        self._table = table
        self._default = default_provider
        self._models = provider_models

    def preferences(self, stage: TaskStage) -> list[str]:
        """The ordered provider preference for ``stage`` (default provider if unset)."""
        return list(self._table.get(stage.value) or [self._default])

    def select(self, stage: TaskStage, allowed_providers: Sequence[str]) -> RoutingDecision:
        """Pick the first preferred provider that is configured and in
        ``allowed_providers`` (the locality-authorized set).

        The first preference winning is not a fallback; any later one is
        (``fallback=True``) so the audit trail shows the preferred provider was
        skipped. No eligible provider raises :class:`NoEligibleProviderError`.
        """
        prefs = self.preferences(stage)
        allowed = set(allowed_providers)
        for index, provider in enumerate(prefs):
            if provider in allowed and provider in self._models:
                reason = "preferred" if index == 0 else f"fallback:{prefs[0]}_unavailable"
                return RoutingDecision(
                    stage=stage, provider=provider, model=self._models[provider],
                    reason=reason, fallback=index > 0,
                )
        raise NoEligibleProviderError(
            f"no configured, authorized provider for stage {stage.value!r} "
            f"among preferences {prefs}"
        )


__all__ = ["NoEligibleProviderError", "RoutingTable"]
