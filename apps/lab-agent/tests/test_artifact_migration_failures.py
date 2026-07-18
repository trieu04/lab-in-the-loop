"""Failure-isolation, secret-hygiene, and CLI tests for the artifact migration.

Covers: a malformed connection response fails that item visibly without drawing
a guessed edge; a missing/malformed public base URL blocks before any write;
output never leaks a capability token or full URL; and the standalone CLI parser
documents the mirror-only / no-deletion contract.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from lab_agent import artifact_migration as migration
from lab_agent.config import Settings
from lab_agent.state_store import StateStore
from tests.fakes import FakeMCP

BASE = "https://lab.test"
_SCRIPT = (
    Path(__file__).resolve().parents[3] / "scripts" / "migrate-generated-notes-to-browser-artifacts.py"
)


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "state.db")
    yield s
    s.close()


def _load_cli() -> Any:
    spec = importlib.util.spec_from_file_location("migrate_notes_cli", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _MalformedConnMCP(FakeMCP):
    """Returns an unreadable ``check_widget_connections`` shape (entry missing
    ``other``); everything else behaves like the normal live fake."""

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "check_widget_connections":
            return json.dumps(
                {"found": True, "incoming": [{"connector_id": "x", "direction": "in"}], "outgoing": []}
            )
        return await super().call_tool(name, arguments)


async def test_malformed_connection_shape_fails_item_without_guessed_edges(store):
    mcp = _MalformedConnMCP(live=True)
    mcp.seed_widget("setup1", "Note", title="[EXP:Setup v001] A+B", text="mix")
    mcp.seed_connector("idea1", "setup1")  # a real edge exists, but its shape is unreadable
    settings = Settings(artifact_public_base_url=BASE)

    summary = await migration.run_migration(mcp, store, settings, canvas_id="c", apply=True)

    assert summary.failed == 1
    item = summary.items[0]
    assert item.note_id == "setup1" and item.status == "failed" and item.error
    # Nothing guessed or written: no Browser, no artifact, no new connector.
    assert [w for w in mcp.notes.values() if w["widget_type"] == "Browser"] == []
    assert store.conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
    assert mcp.connectors == [("idea1", "setup1")]


@pytest.mark.parametrize("base", ["", "not-a-url", "ftp://lab.test"])
async def test_missing_or_malformed_base_url_blocks_before_any_write(store, base):
    mcp = FakeMCP(live=True)
    mcp.seed_widget("setup1", "Note", title="[EXP:Setup v001] A+B", text="mix")
    with pytest.raises(migration.ArtifactUrlError):
        await migration.run_migration(
            mcp, store, Settings(artifact_public_base_url=base), canvas_id="c", apply=True
        )
    assert [w for w in mcp.notes.values() if w["widget_type"] == "Browser"] == []
    assert store.conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0


async def test_summary_output_contains_no_token_or_full_url(store):
    mcp = FakeMCP(live=True)
    mcp.seed_widget("idea1", "Note", text="{idea: A+B}")
    mcp.seed_widget("setup1", "Note", title="[EXP:Setup v001] A+B", text="mix")
    mcp.seed_connector("idea1", "setup1")
    summary = await migration.run_migration(
        mcp, store, Settings(artifact_public_base_url=BASE), canvas_id="c", apply=True
    )

    blob = json.dumps([vars(item) for item in summary.items]) + json.dumps(_load_cli().summary_dict(summary))
    assert "token=" not in blob
    assert "/artifacts/" not in blob
    assert BASE not in blob
    assert summary.items[0].widget_id  # a widget id IS surfaced (safe: an id, not a URL)


def test_cli_dry_run_does_not_open_durable_state(monkeypatch):
    cli = _load_cli()
    mcp = FakeMCP(live=True)
    mcp.seed_widget("setup1", "Note", title="[EXP:Setup v001] A+B", text="mix")

    class _Client:
        async def __aenter__(self):
            return mcp

        async def __aexit__(self, *_args):
            return None

    def _unexpected_runtime(_settings):
        raise AssertionError("dry-run must not open or migrate the state database")

    monkeypatch.setattr(cli, "get_settings", Settings)
    monkeypatch.setattr(cli, "MCPClient", lambda _url: _Client())
    monkeypatch.setattr(cli, "build_runtime_context", _unexpected_runtime)

    code = asyncio.run(
        cli._run(argparse.Namespace(canvas="c", apply=False, as_json=True))
    )
    assert code == 0


def test_cli_help_states_mirror_only_and_no_deletion():
    help_text = _load_cli().build_parser().format_help().lower()
    assert "mirror" in help_text
    assert "delet" in help_text  # 'deleted' / 'deletion'
    assert "dry-run" in help_text


def test_cli_requires_canvas_and_parses_apply_flag():
    parser = _load_cli().build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])  # --canvas is required
    dry = parser.parse_args(["--canvas", "c"])
    assert dry.canvas == "c" and dry.apply is False and dry.as_json is False
    applied = parser.parse_args(["--canvas", "c", "--apply", "--json"])
    assert applied.apply is True and applied.as_json is True
