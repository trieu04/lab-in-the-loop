"""In-memory fakes for unit tests (no network)."""

from __future__ import annotations

import json
from typing import Any

from lab_agent.adapters.base import AdapterResponse, Message, ToolSpec
from lab_agent.tool_bridge import READ_TOOLS


class FakeMCP:
    """A stand-in for :class:`lab_agent.mcp_client.MCPClient`.

    Records created notes/connectors so tests can assert the canvas graph, and
    returns a preset ``scan_experiment_workflow`` snapshot.
    """

    def __init__(
        self,
        note_text: dict[str, str] | None = None,
        workflow: dict[str, Any] | None = None,
    ) -> None:
        self.notes: dict[str, dict[str, Any]] = {}
        self.connectors: list[tuple[str, str]] = []
        self.workflow = workflow or {}
        self._counter = 0
        for wid, text in (note_text or {}).items():
            self.notes[wid] = {"id": wid, "text": text}

    async def list_tools(self) -> list[ToolSpec]:
        return [ToolSpec(name=n, description="", parameters={"type": "object"}) for n in READ_TOOLS]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "create_note":
            self._counter += 1
            wid = f"note{self._counter}"
            self.notes[wid] = {"id": wid, "text": arguments["text"], "title": arguments.get("title")}
            return json.dumps({"id": wid})
        if name == "create_connector":
            self._counter += 1
            self.connectors.append((arguments["src_widget_id"], arguments["dst_widget_id"]))
            return json.dumps({"id": f"conn{self._counter}"})
        if name == "get_note":
            return json.dumps(self.notes.get(arguments["note_id"], {}))
        if name == "check_ragcluster_connections":
            return json.dumps({"clusters": []})
        if name == "scan_experiment_workflow":
            return json.dumps({**self.workflow, "canvas_id": arguments.get("canvas_id")})
        return json.dumps({})


class ScriptedAdapter:
    """Adapter returning canned structured outputs; never calls tools.

    ``structured`` maps a schema name to a dict (returned every call) or a list
    (a queue — each call pops the next, the last value repeats).
    """

    def __init__(self, structured: dict[str, Any]) -> None:
        self._structured = {k: list(v) if isinstance(v, list) else v for k, v in structured.items()}
        self.schema_calls: list[str] = []

    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        response_schema: dict[str, Any] | None = None,
        schema_name: str = "result",
    ) -> AdapterResponse:
        if response_schema is None:
            return AdapterResponse(text="grounded summary", tool_calls=[])
        self.schema_calls.append(schema_name)
        value = self._structured[schema_name]
        if isinstance(value, list):
            return AdapterResponse(parsed=value.pop(0) if len(value) > 1 else value[0])
        return AdapterResponse(parsed=value)
