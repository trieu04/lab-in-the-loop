"""Experiment-prepare action: turn an {exp:} Note into an OpenAI experiment plan.

Triggered when a Connector runs from a RagCluster (src) to a Note (dst) whose
text contains ``{exp: <chat content>}``. Calls the OpenAI API and creates a new
result Note holding the generated experiment preparation. Job label:
"Analyzing and prepare new setup".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from openai import AsyncOpenAI

from app.config import Settings
from app.jobs.registry import ActionResult, action

if TYPE_CHECKING:
    import aiosqlite
    from canvus_sdk import Client

log = structlog.get_logger(__name__)

PROMPT_PREFIX = "Please give a short mockup medical experiment prepare, "

# Cached settings instance — avoids repeated env reads on every action invocation.
_settings: Settings | None = None


def _get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings


@action(
    name="experiment_prepare",
    description="Analyzing and prepare new setup: OpenAI experiment prep from an {exp:} Note.",
    complexity="complex",
    estimated_seconds=30,
)
async def handle_experiment_prepare(
    payload: dict,
    db: aiosqlite.Connection,
    client: Client,
) -> ActionResult:
    """Handle experiment_prepare action.

    Payload: {
        "canvas_id": str,
        "note_id": str,               # the {exp:} Note (connector dst)
        "ragcluster_widget_id": str,  # the RagCluster Image (connector src)
        "ragcluster_id": str,
        "exp_content": str,           # chat content extracted from {exp: ...}
        "exp_hash": str,
    }
    """
    canvas_id = payload.get("canvas_id")
    note_id = payload.get("note_id")
    exp_content = payload.get("exp_content", "")

    if not all([canvas_id, note_id, exp_content]):
        return ActionResult.err("canvas_id, note_id, exp_content required")

    settings = _get_settings()
    if not settings.openai_api_key:
        return ActionResult.err("CANVUS_OPENAI_API_KEY not configured")

    try:
        # 1. Mark the source Note as processing (best-effort).
        try:
            await client.widgets.notes.update(
                canvas_id,
                note_id,
                {"text": f"{{exp: {exp_content}}}\n\nStatus: Analyzing..."},
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "exp_source_note_update_failed",
                canvas_id=canvas_id,
                note_id=note_id,
                error=str(exc),
            )

        # 2. Query OpenAI with the fixed prompt + the chat content.
        openai_client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
        )
        completion = await openai_client.chat.completions.create(
            model=settings.openai_model,
            messages=[{"role": "user", "content": PROMPT_PREFIX + exp_content}],
        )
        answer = (completion.choices[0].message.content or "").strip()
        if not answer:
            return ActionResult.err("OpenAI returned an empty response")

        log.info(
            "experiment_prepare_generated",
            canvas_id=canvas_id,
            note_id=note_id,
            model=settings.openai_model,
            answer_len=len(answer),
        )

        # 3. Create the result Note holding the experiment prep.
        result_note = await client.widgets.notes.create(
            canvas_id,
            {"text": answer, "title": f"Experiment Prep: {exp_content[:40]}"},
        )
        result_note_id = getattr(result_note, "id", "") or ""

        # 4. Connect source Note -> result Note.
        if result_note_id:
            await client.widgets.connectors.create(
                canvas_id,
                {"src": {"id": note_id}, "dst": {"id": result_note_id}},
            )

        # 5. Mark the source Note as completed (best-effort).
        try:
            await client.widgets.notes.update(
                canvas_id,
                note_id,
                {"text": f"{{exp: {exp_content}}}\n\nStatus: Completed"},
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "exp_source_note_update_failed",
                canvas_id=canvas_id,
                note_id=note_id,
                error=str(exc),
            )

        log.info(
            "experiment_prepare_done",
            canvas_id=canvas_id,
            note_id=note_id,
            result_note_id=result_note_id,
        )
        return ActionResult.ok(
            result_note_id=result_note_id,
            canvas_id=canvas_id,
            note=f"Created experiment-prep note {result_note_id}",
        )

    except Exception as exc:
        log.error(
            "experiment_prepare_failed",
            canvas_id=canvas_id,
            note_id=note_id,
            error=str(exc),
        )
        return ActionResult.err(str(exc))
