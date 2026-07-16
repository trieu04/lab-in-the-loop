"""Tests for the experiment_prepare flow: {exp:} parser, dedup, action, scanner dispatch."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.migrations import run_migrations
from app.jobs.queue import _compute_dedup_key
from app.jobs.queue import enqueue as _enqueue
from app.scanner.engine import CanvasWatcher
from app.scanner.graph import ConnectorIndex
from app.scanner.parser import CommandParser


async def _init_db() -> Any:
    import aiosqlite

    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await run_migrations(conn)
    return conn


# ── parser: extract_exp ───────────────────────────────────────────────


def test_extract_exp_matches_single_brace() -> None:
    assert CommandParser.extract_exp("{exp: cure cancer}") == "cure cancer"
    assert CommandParser.extract_exp("prefix {exp:  spaced  } suffix") == "spaced"


def test_extract_exp_ignores_double_brace_query() -> None:
    assert CommandParser.extract_exp("{{ What is pneumonia? }}") is None


def test_extract_exp_empty_and_oversized() -> None:
    assert CommandParser.extract_exp("") is None
    assert CommandParser.extract_exp("no marker here") is None
    assert CommandParser.extract_exp("{exp:   }") is None
    assert CommandParser.extract_exp("{exp: " + "x" * 5000 + "}") is None


# ── dedup key ──────────────────────────────────────────────────────────


def test_experiment_dedup_key() -> None:
    key = _compute_dedup_key(
        "experiment_prepare",
        {"canvas_id": "c1", "note_id": "n1", "exp_hash": "h1"},
    )
    assert key == "c1|n1|h1"


def test_experiment_dedup_key_incomplete_is_none() -> None:
    assert _compute_dedup_key(
        "experiment_prepare", {"canvas_id": "c1", "note_id": "n1"}
    ) is None


@pytest.mark.asyncio
async def test_experiment_dedup_skips_duplicate() -> None:
    db = await _init_db()
    payload = {"canvas_id": "c1", "note_id": "n1", "exp_hash": "h1", "exp_content": "x"}
    job1 = await _enqueue(db, "experiment_prepare", payload)
    job2 = await _enqueue(db, "experiment_prepare", payload)
    assert job1 is not None
    assert job2 is None
    await db.close()


# ── action: handle_experiment_prepare ─────────────────────────────────


class _FakeNotes:
    def __init__(self, note_id: str = "result_note_001") -> None:
        self._note_id = note_id
        self.created: list[dict] = []

    async def create(self, canvas_id: str, data: dict) -> Any:
        self.created.append(data)
        return SimpleNamespace(id=self._note_id)

    async def update(self, canvas_id: str, note_id: str, data: dict) -> None:
        pass


class _FakeConnectors:
    def __init__(self) -> None:
        self.created: list[dict] = []

    async def create(self, canvas_id: str, data: dict) -> None:
        self.created.append(data)


class _FakeClient:
    def __init__(self) -> None:
        self.widgets = SimpleNamespace(notes=_FakeNotes(), connectors=_FakeConnectors())


def _fake_settings(api_key: str = "sk-test") -> Any:
    return SimpleNamespace(
        openai_api_key=api_key,
        openai_model="gpt-4o-mini",
        openai_base_url="",
    )


def _mock_openai(answer: str) -> MagicMock:
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=answer))]
    )
    instance = MagicMock()
    instance.chat.completions.create = AsyncMock(return_value=completion)
    factory = MagicMock(return_value=instance)
    return factory


@pytest.mark.asyncio
async def test_experiment_prepare_creates_result_note() -> None:
    db = await _init_db()
    client = _FakeClient()

    from app.actions import experiment_prepare as mod

    with (
        patch.object(mod, "_get_settings", return_value=_fake_settings()),
        patch.object(mod, "AsyncOpenAI", _mock_openai("Experiment steps: 1, 2, 3")),
    ):
        result = await mod.handle_experiment_prepare(
            payload={
                "canvas_id": "canvas_1",
                "note_id": "note_exp",
                "ragcluster_widget_id": "rag_1",
                "ragcluster_id": "RAGCluster_ABC",
                "exp_content": "test idea",
                "exp_hash": "hash_1",
            },
            db=db,
            client=client,  # type: ignore[arg-type]
        )

    assert result.success is True
    assert result.data["result_note_id"] == "result_note_001"
    # Result note carries the OpenAI answer.
    assert client.widgets.notes.created[0]["text"] == "Experiment steps: 1, 2, 3"
    # Source -> result connector wired.
    assert client.widgets.connectors.created[0] == {
        "src": {"id": "note_exp"},
        "dst": {"id": "result_note_001"},
    }
    await db.close()


@pytest.mark.asyncio
async def test_experiment_prepare_missing_fields() -> None:
    db = await _init_db()
    client = _FakeClient()
    from app.actions import experiment_prepare as mod

    result = await mod.handle_experiment_prepare(
        payload={"canvas_id": "canvas_1", "note_id": "note_exp"},
        db=db,
        client=client,  # type: ignore[arg-type]
    )
    assert result.success is False
    assert "exp_content" in result.error
    await db.close()


@pytest.mark.asyncio
async def test_experiment_prepare_no_api_key() -> None:
    db = await _init_db()
    client = _FakeClient()
    from app.actions import experiment_prepare as mod

    with patch.object(mod, "_get_settings", return_value=_fake_settings(api_key="")):
        result = await mod.handle_experiment_prepare(
            payload={
                "canvas_id": "canvas_1",
                "note_id": "note_exp",
                "exp_content": "idea",
                "exp_hash": "h",
            },
            db=db,
            client=client,  # type: ignore[arg-type]
        )
    assert result.success is False
    assert "OPENAI_API_KEY" in result.error
    await db.close()


# ── scanner dispatch: RagCluster → Note ({exp:}) ──────────────────────


class _FakeWidget:
    def __init__(
        self, widget_id: str, widget_type: str, title: str = "", text: str = ""
    ) -> None:
        self.id = widget_id
        self.widget_type = widget_type
        self.title = title
        self.text = text


class _FakeConnector:
    def __init__(self, widget_id: str, src: str, dst: str) -> None:
        self.id = widget_id
        self.widget_type = "Connector"
        self.src = SimpleNamespace(id=src)
        self.dst = SimpleNamespace(id=dst)


class _ListClient:
    def __init__(self, widgets: list[Any]) -> None:
        self.widgets = SimpleNamespace(list=AsyncMock(return_value=widgets))


def _make_watcher(db: Any, widgets: list[Any]) -> CanvasWatcher:
    return CanvasWatcher(canvas_id="canvas_1", client=_ListClient(widgets), db=db)


@pytest.mark.asyncio
async def test_connector_ragcluster_to_exp_note_enqueues_experiment() -> None:
    db = await _init_db()
    widgets = [
        _FakeWidget("rag_1", "Image", title="RAGCluster_ABC"),
        _FakeWidget("note_exp", "Note", text="{exp: run a trial}"),
    ]
    watcher = _make_watcher(db, widgets)
    connector = _FakeConnector("conn_1", src="rag_1", dst="note_exp")
    index = ConnectorIndex.build(widgets)

    with patch("app.scanner.engine.enqueue", new_callable=AsyncMock) as mock_enqueue:
        mock_enqueue.return_value = "job_exp_001"
        await watcher._enqueue_pdf_ingest_or_query(connector, index)

    mock_enqueue.assert_awaited_once()
    action = mock_enqueue.call_args[0][1]
    assert action.__name__ == "handle_experiment_prepare"
    payload = mock_enqueue.call_args[0][2]
    assert payload["note_id"] == "note_exp"
    assert payload["ragcluster_widget_id"] == "rag_1"
    assert payload["exp_content"] == "run a trial"
    assert payload["exp_hash"]
    await db.close()


@pytest.mark.asyncio
async def test_connector_ragcluster_to_plain_note_skipped() -> None:
    db = await _init_db()
    widgets = [
        _FakeWidget("rag_1", "Image", title="RAGCluster_ABC"),
        _FakeWidget("note_plain", "Note", text="just a note, no marker"),
    ]
    watcher = _make_watcher(db, widgets)
    connector = _FakeConnector("conn_2", src="rag_1", dst="note_plain")
    index = ConnectorIndex.build(widgets)

    with patch("app.scanner.engine.enqueue", new_callable=AsyncMock) as mock_enqueue:
        await watcher._enqueue_pdf_ingest_or_query(connector, index)

    mock_enqueue.assert_not_awaited()
    await db.close()
