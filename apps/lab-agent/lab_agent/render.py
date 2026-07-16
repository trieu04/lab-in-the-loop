"""Render structured schema objects into readable Note bodies."""

from __future__ import annotations

from lab_agent.models.experiment import ExperimentResult, ExperimentSetup, LoopDecision


def _bullets(label: str, items: list[str]) -> str:
    if not items:
        return ""
    lines = "\n".join(f"- {it}" for it in items)
    return f"{label}:\n{lines}\n"


def _citation_lines(s: ExperimentSetup) -> str:
    items = [c.source_id + (f" ({c.note})" if c.note else "") for c in s.citations]
    return _bullets("Citations", items)


def _ambiguity_lines(s: ExperimentSetup) -> str:
    items = [
        f"{f.term} -> {f.expansion}" if f.resolved and f.expansion else f"{f.term} (unresolved)"
        for f in s.ambiguity_flags
    ]
    return _bullets("Ambiguity flags", items)


def render_setup(s: ExperimentSetup) -> str:
    hypothesis = f"Hypothesis: {s.hypothesis}\n\n" if s.hypothesis else ""
    evidence = f"Evidence status: {s.evidence_status.value}\n\n" if s.evidence_status else ""
    confidence = f"Confidence: {s.confidence}\n\n" if s.confidence is not None else ""
    return (
        f"{hypothesis}"
        f"Rationale: {s.rationale}\n\n"
        f"{evidence}"
        f"{confidence}"
        f"{_bullets('Inputs', s.inputs)}"
        f"{_bullets('Conditions', s.conditions)}"
        f"{_bullets('Steps', s.steps)}"
        f"{_bullets('Parameters', s.parameters)}"
        f"{_bullets('Expected readouts', s.expected_readouts)}"
        f"{_bullets('Success criteria', s.success_criteria)}"
        f"{_bullets('Constraints', s.constraints)}"
        f"{_citation_lines(s)}"
        f"{_ambiguity_lines(s)}"
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
