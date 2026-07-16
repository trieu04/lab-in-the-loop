"""Emit stages of the experiment loop: the model reads/emits a structured node.

Split from :mod:`lab_agent.orchestrator_support` (which keeps the canvas *write*
stages) to hold each source file under the 200-line budget. Emit helpers are
fail-closed -- they return ``None`` on malformed output rather than fabricate
required fields, so the caller skips the write and does not retry within the
cycle (docs/code-standards.md § structured output).
"""

from __future__ import annotations

from typing import Any, TypeVar

import structlog
from pydantic import BaseModel, ValidationError

from lab_agent import prompts
from lab_agent.adapters.base import Message, ModelAdapter
from lab_agent.config import Settings
from lab_agent.evidence import EvidenceLedger
from lab_agent.loop import ToolLoopLimits, emit_structured, run_tool_loop
from lab_agent.mcp_client import MCPClient
from lab_agent.model_boundary import model_argument_limits, model_text_limits
from lab_agent.models.experiment import ExperimentResult, ExperimentSetup, LoopDecision
from lab_agent.result_safety import ResultLimits, sanitize_text
from lab_agent.tool_bridge import select_read_tools

log = structlog.get_logger(__name__)


_ModelT = TypeVar("_ModelT", bound=BaseModel)


class SchemaValidationError(Exception):
    """Raised when structured output fails schema validation.

    Fail-closed policy (docs/code-standards.md § Structured output): never
    fabricate missing required fields. Callers log a structured warning and skip
    the write, leaving the canvas pending for the next poll.
    """

    def __init__(self, model_name: str, payload: dict[str, Any]) -> None:
        super().__init__(f"{model_name} failed schema validation")
        self.model_name = model_name
        self.payload = payload


def coerce_or_fail(model_cls: type[BaseModel], parsed: dict[str, Any]) -> Any:
    """Validate model output; raise SchemaValidationError instead of filling gaps."""
    try:
        return model_cls.model_validate(parsed)
    except ValidationError as exc:
        raise SchemaValidationError(model_cls.__name__, dict(parsed)) from exc


async def _emit_validated(
    adapter: ModelAdapter, messages: list[Message], model_cls: type[_ModelT], stage: str,
    transcript_max_bytes: int = 256 * 1024,
) -> _ModelT | None:
    """Emit and validate one structured node; on schema failure log and return
    ``None`` (fail-closed, never fabricated fields) so callers skip the write."""
    try:
        return coerce_or_fail(
            model_cls,
            await emit_structured(
                adapter, messages, model_cls.model_json_schema(), model_cls.__name__,
                transcript_max_bytes=transcript_max_bytes,
            ),
        )
    except SchemaValidationError as exc:
        log.warning("schema_validation_failed", stage=stage, model=exc.model_name, payload=exc.payload)
        return None


async def ground_and_emit_setup(
    mcp: MCPClient,
    adapter: ModelAdapter,
    settings: Settings,
    *,
    canvas_id: str,
    idea_text: str,
    ragcluster_id: str,
    prior: str = "",
    ledger: EvidenceLedger | None = None,
) -> ExperimentSetup | None:
    """Ground on the RagCluster + idea and emit an ExperimentSetup (no write).

    ``ledger``, if given, records every successful read the model performs so
    the grounding gate can validate the setup's citations against it.
    """
    read_tools = select_read_tools(await mcp.list_tools(), settings.mcp_server_namespace)
    hint = f" Knowledge scope RagCluster id: {ragcluster_id}." if ragcluster_id else ""
    user = f"Canvas id: {canvas_id}.{hint}\n\nExperiment idea: {idea_text}"
    if prior:
        user += f"\n\nBuild on the previous round:\n{prior}"
    result_limits = model_text_limits(settings)
    safe_user = sanitize_text(user, result_limits)
    if safe_user is None:
        log.warning("model_input_unavailable", stage="setup")
        return None
    messages: list[Message] = [
        {"role": "system", "content": prompts.SETUP_SYSTEM},
        {"role": "user", "content": safe_user},
    ]
    await run_tool_loop(
        adapter, mcp, messages, read_tools, settings.max_tool_steps, ledger,
        namespace=settings.mcp_server_namespace, limits=result_limits,
        argument_limits=model_argument_limits(settings),
        run_limits=ToolLoopLimits(
            settings.model_tool_calls_per_turn, settings.model_tool_calls_per_run,
            settings.model_transcript_max_bytes,
        ),
    )
    return await _emit_validated(
        adapter, messages, ExperimentSetup, "setup", settings.model_transcript_max_bytes,
    )


async def emit_result(
    adapter: ModelAdapter, setup_text: str, *, settings: Settings | None = None,
) -> ExperimentResult | None:
    """Emit an ExperimentResult for a rendered setup (no write)."""
    limits = model_text_limits(settings) if settings else ResultLimits()
    user = sanitize_text(f"Experiment setup:\n{setup_text}", limits)
    if user is None:
        log.warning("model_input_unavailable", stage="result")
        return None
    messages: list[Message] = [
        {"role": "system", "content": prompts.RESULT_SYSTEM}, {"role": "user", "content": user},
    ]
    cap = settings.model_transcript_max_bytes if settings else 256 * 1024
    return await _emit_validated(adapter, messages, ExperimentResult, "result", cap)


async def emit_decision(
    adapter: ModelAdapter, setup_text: str, result_text: str, *, settings: Settings | None = None,
) -> LoopDecision | None:
    """Emit a LoopDecision for a setup/result pair (no write)."""
    limits = model_text_limits(settings) if settings else ResultLimits()
    user = sanitize_text(f"Setup:\n{setup_text}\n\nResult:\n{result_text}", limits)
    if user is None:
        log.warning("model_input_unavailable", stage="decision")
        return None
    messages: list[Message] = [
        {"role": "system", "content": prompts.DECIDE_SYSTEM}, {"role": "user", "content": user},
    ]
    cap = settings.model_transcript_max_bytes if settings else 256 * 1024
    return await _emit_validated(adapter, messages, LoopDecision, "decision", cap)


__all__ = [
    "SchemaValidationError",
    "coerce_or_fail",
    "emit_decision",
    "emit_result",
    "ground_and_emit_setup",
]
