"""In-memory fakes for unit tests (no network)."""

from __future__ import annotations

import json
from typing import Any

from lab_agent.adapters.base import AdapterResponse, Message, ToolSpec
from lab_agent.tool_bridge import READ_TOOLS
from tests import canvus_detector_bridge as detector


class FakeMCP:
    """A stand-in for :class:`lab_agent.mcp_client.MCPClient`.

    Records created notes/connectors so tests can assert the canvas graph.
    In the default (static) mode, ``scan_experiment_workflow`` returns the
    preset ``workflow`` fixture verbatim. In live mode (``live=True``), it is
    recomputed on every call from ``self.notes``/``self.connectors`` via the
    real canvus-mcp detector — needed for regression tests that must observe
    what the orchestrator's own writes do to the *next* scan (e.g. that a
    round-advance edge is not re-detected as an actionable loop).
    """

    def __init__(
        self,
        note_text: dict[str, str] | None = None,
        workflow: dict[str, Any] | None = None,
        *,
        live: bool = False,
    ) -> None:
        self.notes: dict[str, dict[str, Any]] = {}
        self.connectors: list[tuple[str, str]] = []
        self.workflow = workflow or {}
        self.live = live
        self._counter = 0
        self._fault_queue: dict[str, list[str]] = {}
        self._error_payload_queue: dict[str, list[str]] = {}
        for wid, text in (note_text or {}).items():
            self.seed_widget(wid, "Note", text=text)

    def fail_next(self, tool_name: str, *, times: int = 1, error: str = "simulated failure") -> None:
        """Queue ``times`` deterministic failures for the next calls to ``tool_name``.

        Each queued call raises ``RuntimeError(error)`` instead of running
        normally, standing in for a transport/connection-level crash (mid-flight
        node/connector write, or a probe/tool-call that never returns) --
        distinct from :meth:`fail_next_as_error_payload`, which simulates the
        real MCP server's *non-raising* tool-level failure shape. Deterministic
        (no sleeps, no randomness): the Nth call to ``tool_name`` after this is
        queued fails, every call after the queue drains succeeds normally.
        """
        self._fault_queue.setdefault(tool_name, []).extend([error] * times)

    def fail_next_as_error_payload(self, tool_name: str, *, times: int = 1, error: str = "simulated failure") -> None:
        """Queue ``times`` non-raising ``{"error": ...}`` JSON responses for ``tool_name``.

        Mirrors the real (non-fake) ``MCPClient.call_tool``'s actual behaviour
        on a tool-level failure (``result.isError``): it returns normally with
        an error-shaped JSON string, it does not raise (see ``mcp_client.py``).
        This is the "phantom success" shape write helpers must fail closed on
        -- separate from :meth:`fail_next`'s raised-exception fault, which a
        caller already sees as an error without any extra handling.
        """
        self._error_payload_queue.setdefault(tool_name, []).extend([error] * times)

    def seed_widget(
        self, wid: str, widget_type: str, *, title: str = "", text: str = "", url: str = ""
    ) -> None:
        """Record a canvas widget (id, type, title, text, url); also seeds live mode.

        Browser widgets carry a ``url`` (the capability-protected artifact URL);
        Notes leave it empty. One unified widget map so the canvus-mcp detector
        bridge (``live_scan``/``check_widget_connections``) classifies Notes and
        generated Browser widgets from the same source, as production does.
        """
        self.notes[wid] = {"id": wid, "widget_type": widget_type, "title": title, "text": text, "url": url}

    def seed_connector(self, src: str, dst: str) -> None:
        """Add a pre-existing connector, for live-recompute mode."""
        self.connectors.append((src, dst))

    async def list_tools(self) -> list[ToolSpec]:
        return [ToolSpec(name=n, description="", parameters={"type": "object"}) for n in READ_TOOLS]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        queue = self._fault_queue.get(name)
        if queue:
            raise RuntimeError(queue.pop(0))
        error_queue = self._error_payload_queue.get(name)
        if error_queue:
            return json.dumps({"error": error_queue.pop(0)})
        if name == "create_note":
            self._counter += 1
            wid = f"note{self._counter}"
            self.seed_widget(wid, "Note", title=arguments.get("title", "") or "", text=arguments["text"])
            return json.dumps({"id": wid})
        if name == "create_browser":
            self._counter += 1
            wid = f"browser{self._counter}"
            self.seed_widget(
                wid, "Browser", title=arguments.get("title", "") or "", url=arguments.get("url", "") or ""
            )
            return json.dumps({"id": wid})
        if name == "update_browser":
            wid = arguments["browser_id"]
            widget = self.notes.get(wid)
            if widget is None:
                return json.dumps({"error": f"unknown browser {wid}"})
            if "url" in arguments:
                widget["url"] = arguments["url"]
            if "title" in arguments:
                widget["title"] = arguments["title"]
            return json.dumps({"id": wid})
        if name == "create_connector":
            self._counter += 1
            self.connectors.append((arguments["src_widget_id"], arguments["dst_widget_id"]))
            return json.dumps({"id": f"conn{self._counter}"})
        if name == "get_note":
            return json.dumps(self.notes.get(arguments["note_id"], {}))
        if name == "check_ragcluster_connections":
            return json.dumps({"clusters": []})
        if name == "check_widget_connections":
            return json.dumps(
                detector.check_widget_connections(
                    self.notes, self.connectors, arguments["widget_id"], arguments.get("canvas_id")
                )
            )
        if name == "scan_experiment_workflow":
            snap = detector.live_scan(self.notes, self.connectors) if self.live else dict(self.workflow)
            return json.dumps({**snap, "canvas_id": arguments.get("canvas_id")})
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
