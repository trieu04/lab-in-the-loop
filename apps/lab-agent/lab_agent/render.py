"""Render structured schema objects into readable Note bodies."""

from __future__ import annotations

from lab_agent.models.experiment import ExperimentResult, ExperimentSetup, LoopDecision


def _bullets(label: str, items: list[str]) -> str:
    if not items:
        return ""
    lines = "\n".join(f"- {it}" for it in items)
    return f"{label}:\n{lines}\n"


def render_setup(s: ExperimentSetup) -> str:
    return (
        f"Rationale: {s.rationale}\n\n"
        f"{_bullets('Inputs', s.inputs)}"
        f"{_bullets('Conditions', s.conditions)}"
        f"{_bullets('Steps', s.steps)}"
        f"{_bullets('Parameters', s.parameters)}"
        f"{_bullets('Expected readouts', s.expected_readouts)}"
    )


def render_result(r: ExperimentResult) -> str:
    return (
        f"Summary: {r.summary}\n\n"
        f"{_bullets('Observations', r.observations)}"
        f"{_bullets('Metrics', r.metrics)}"
        f"{_bullets('Quality flags', r.quality_flags)}"
    )


def render_decision(d: LoopDecision) -> str:
    verb = "CONTINUE" if d.proceed else "STOP"
    focus = f"\n\nNext focus: {d.next_focus}" if (d.proceed and d.next_focus) else ""
    return f"Decision: {verb}\n\nReason: {d.reason}{focus}"


__all__ = ["render_decision", "render_result", "render_setup"]
