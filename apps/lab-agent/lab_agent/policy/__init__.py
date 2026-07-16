"""Governance policy stack: routing, locality, budgets, and stop rules.

Behaviour for the typed vocabulary in :mod:`lab_agent.models.governance`. The
builders here translate flat :class:`~lab_agent.config.Settings` into the policy
objects the model gateway and orchestrator use, keeping every provider mapping
in configuration rather than in workflow code (NFR-LITL-003).
"""

from __future__ import annotations

from lab_agent.config import Settings
from lab_agent.policy.budget import (
    BudgetExceededError,
    GovernanceBudget,
    PostResponseBudgetExceededError,
    Reservation,
)
from lab_agent.policy.locality import LocalityPolicy
from lab_agent.policy.pricing import estimate_cost
from lab_agent.policy.routing import NoEligibleProviderError, RoutingTable
from lab_agent.policy.stop import StopPolicy, result_signature


def provider_model_map(settings: Settings) -> dict[str, str]:
    """The configured model id for each provider the agent can construct."""
    return {"openai": settings.openai_model, "claude": settings.anthropic_model}


def build_routing_table(settings: Settings) -> RoutingTable:
    return RoutingTable(
        dict(settings.routing_table),
        default_provider=settings.model_provider,
        provider_models=provider_model_map(settings),
    )


def build_locality_policy(settings: Settings) -> LocalityPolicy:
    return LocalityPolicy(
        dict(settings.provider_data_classifications), dict(settings.provider_endpoints)
    )


def build_stop_policy(settings: Settings) -> StopPolicy:
    return StopPolicy(
        max_rounds=settings.loop_max_rounds,
        wall_time_budget_seconds=settings.wall_time_budget_seconds,
        no_progress_rounds=settings.no_progress_rounds,
    )


__all__ = [
    "BudgetExceededError", "GovernanceBudget", "LocalityPolicy", "NoEligibleProviderError",
    "PostResponseBudgetExceededError", "Reservation", "RoutingTable", "StopPolicy",
    "build_locality_policy", "build_routing_table",
    "build_stop_policy", "estimate_cost", "provider_model_map", "result_signature",
]
