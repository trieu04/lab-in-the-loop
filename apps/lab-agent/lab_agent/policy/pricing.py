"""Versioned cost estimation from normalized usage (NFR-LITL-009).

Cost is an *estimate*, never a billed figure, so every result carries the
pricing table version and a :class:`UsageStatus`. Unknown pricing (a model with
no configured rate) and unavailable usage both surface as
:attr:`UsageStatus.UNAVAILABLE` -- a missing rate is never treated as free.
"""

from __future__ import annotations

from lab_agent.models.governance import CostEstimate, Usage, UsageStatus


def estimate_cost(
    usage: Usage, pricing: dict[str, dict[str, float]], pricing_version: str
) -> CostEstimate:
    """Price ``usage`` against the configured per-1k-token rates.

    Returns an ``UNAVAILABLE`` estimate (zero cost, explicitly flagged) when the
    provider gave no usage or the model has no configured pricing; otherwise the
    estimate inherits the usage status (``EXACT`` for measured counts).
    """
    if usage.status is UsageStatus.UNAVAILABLE:
        return CostEstimate(0.0, pricing_version, UsageStatus.UNAVAILABLE)
    rate = pricing.get(usage.model)
    if rate is None or "input_per_1k" not in rate or "output_per_1k" not in rate:
        return CostEstimate(0.0, pricing_version, UsageStatus.UNAVAILABLE)
    cost = (
        usage.prompt_tokens / 1000.0 * rate["input_per_1k"]
        + usage.completion_tokens / 1000.0 * rate["output_per_1k"]
    )
    return CostEstimate(round(cost, 6), pricing_version, usage.status)


__all__ = ["estimate_cost"]
