"""Payload builders for generated experiment artifacts."""

from __future__ import annotations

from typing import Any

from lab_agent import durable_browser, nodes, render
from lab_agent.models.experiment import ExperimentResult, ExperimentSetup


def version(round_index: int) -> str:
    """Format a round index as a zero-padded version tag, e.g. 1 -> 'v001'."""
    return f"v{round_index:03d}"


def setup_payload(
    setup: ExperimentSetup, *, idea_text: str, idea_id: str, round_index: int
) -> tuple[str, dict[str, Any]]:
    """Return the title and canonical payload for a setup round."""
    title = f"{nodes.EXP_SETUP} {version(round_index)}] {idea_text[:40]}"
    body = f"Idea: {idea_id}\nRound: {round_index}\n\n{render.render_setup(setup)}"
    return title, {
        **setup.model_dump(),
        "title": title,
        "round": round_index,
        "idea_id": idea_id,
        durable_browser.RENDERED_TEXT_KEY: body,
    }


def result_payload(
    result: ExperimentResult, *, setup_id: str, round_index: int
) -> tuple[str, dict[str, Any]]:
    """Return the title and canonical payload for a result round."""
    title = f"{nodes.EXP_RESULT} {version(round_index)}]"
    body = f"Setup: {setup_id}\nRound: {round_index}\n\n{render.render_result(result)}"
    return title, {
        **result.model_dump(),
        "title": title,
        "round": round_index,
        "setup_id": setup_id,
        durable_browser.RENDERED_TEXT_KEY: body,
    }


__all__ = ["result_payload", "setup_payload", "version"]
