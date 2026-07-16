"""Tests for the ``serve-artifacts`` CLI command: parser wiring, settings
defaults vs. ``--host``/``--port`` overrides, and that it never calls
``uvicorn.run()`` (this coroutine already runs inside ``cli._run``'s own
event loop -- see ``cli._serve_artifacts``'s docstring).

Uvicorn itself is never started here: ``uvicorn.Config``/``uvicorn.Server``
are monkeypatched so the test only observes what ``_serve_artifacts``
constructs them with.
"""

from __future__ import annotations

import argparse
import asyncio

import pytest

from lab_agent import cli
from lab_agent.config import Settings
from lab_agent.runtime import RuntimeContext
from lab_agent.state_store import StateStore


def test_build_parser_recognizes_serve_artifacts_with_no_overrides() -> None:
    args = cli._build_parser().parse_args(["serve-artifacts"])

    assert args.command == "serve-artifacts"
    assert args.host is None
    assert args.port is None


def test_build_parser_accepts_host_and_port_overrides() -> None:
    args = cli._build_parser().parse_args(
        ["serve-artifacts", "--host", "0.0.0.0", "--port", "9500"]
    )

    assert args.host == "0.0.0.0"
    assert args.port == 9500


@pytest.mark.parametrize("value", ["0", "65536"])
def test_build_parser_rejects_invalid_port(value: str) -> None:
    with pytest.raises(SystemExit):
        cli._build_parser().parse_args(["serve-artifacts", "--port", value])


class _FakeConfig:
    """Records the kwargs ``_serve_artifacts`` would hand ``uvicorn.Config``."""

    last: dict[str, object] = {}

    def __init__(self, app: object, *, host: str, port: int, access_log: bool) -> None:
        _FakeConfig.last = {"host": host, "port": port, "access_log": access_log}


class _FakeServer:
    """Never actually binds a socket -- ``serve()`` returns immediately."""

    def __init__(self, config: _FakeConfig) -> None:
        self.config = config

    async def serve(self) -> None:
        return None


@pytest.fixture
def ctx(tmp_path):
    store = StateStore(tmp_path / "state.db")
    yield RuntimeContext(store=store, runtime_instance_id="rt-test", settings=Settings())
    store.close()


def test_serve_artifacts_falls_back_to_settings_when_not_overridden(monkeypatch, ctx) -> None:
    monkeypatch.setattr(cli.uvicorn, "Config", _FakeConfig)
    monkeypatch.setattr(cli.uvicorn, "Server", _FakeServer)
    settings = Settings(artifact_bind_host="10.0.0.1", artifact_bind_port=9100)  # type: ignore[call-arg]
    args = argparse.Namespace(host=None, port=None)

    code = asyncio.run(cli._serve_artifacts(ctx, settings, args))

    assert code == 0
    assert _FakeConfig.last == {"host": "10.0.0.1", "port": 9100, "access_log": False}


def test_serve_artifacts_cli_overrides_win_over_settings(monkeypatch, ctx) -> None:
    monkeypatch.setattr(cli.uvicorn, "Config", _FakeConfig)
    monkeypatch.setattr(cli.uvicorn, "Server", _FakeServer)
    settings = Settings(artifact_bind_host="10.0.0.1", artifact_bind_port=9100)  # type: ignore[call-arg]
    args = argparse.Namespace(host="192.168.1.1", port=9999)

    code = asyncio.run(cli._serve_artifacts(ctx, settings, args))

    assert code == 0
    assert _FakeConfig.last == {"host": "192.168.1.1", "port": 9999, "access_log": False}


def test_serve_artifacts_never_leaves_access_logging_enabled(monkeypatch, ctx) -> None:
    """Access logs would otherwise print the capability token query string."""
    monkeypatch.setattr(cli.uvicorn, "Config", _FakeConfig)
    monkeypatch.setattr(cli.uvicorn, "Server", _FakeServer)
    settings = Settings()
    args = argparse.Namespace(host=None, port=None)

    asyncio.run(cli._serve_artifacts(ctx, settings, args))

    assert _FakeConfig.last["access_log"] is False
