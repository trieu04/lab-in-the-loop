"""Structured outputs the model emits at each stage of the experiment loop.

The connector-driven workflow: an idea note grounded on a RagCluster becomes an
`ExperimentSetup`; a robot run produces an `ExperimentResult`; analysing the two
yields a `LoopDecision` that either stops the loop or drives the next round.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExperimentSetup(BaseModel):
    """A runnable experiment setup derived from an idea + internal knowledge."""

    rationale: str = Field(..., description="Why this setup follows from the idea and knowledge.")
    inputs: list[str] = Field(
        default_factory=list,
        description="Materials / samples / compounds / reagents required.",
    )
    conditions: list[str] = Field(
        default_factory=list,
        description="Conditions to hold (temperature, time, concentration, ...).",
    )
    steps: list[str] = Field(default_factory=list, description="Ordered protocol steps.")
    parameters: list[str] = Field(
        default_factory=list,
        description="Tunable parameters as 'name=value' strings.",
    )
    expected_readouts: list[str] = Field(
        default_factory=list,
        description="Measurements the run should produce.",
    )


class ExperimentResult(BaseModel):
    """Observed results from a (mock) robot run of a setup."""

    summary: str = Field(..., description="One-line summary of the outcome.")
    observations: list[str] = Field(default_factory=list, description="Key observations.")
    metrics: list[str] = Field(
        default_factory=list,
        description="Quantitative readouts as 'name=value' strings.",
    )
    quality_flags: list[str] = Field(
        default_factory=list,
        description="Quality-control concerns, if any.",
    )


class LoopDecision(BaseModel):
    """Whether the experiment loop should run another round (UC step 3)."""

    proceed: bool = Field(..., description="True to run another experiment round; False to stop.")
    reason: str = Field(..., description="Why continue or stop.")
    next_focus: str = Field(
        default="",
        description="If proceeding, what the next setup should change or explore.",
    )


__all__ = ["ExperimentResult", "ExperimentSetup", "LoopDecision"]
