"""Tests for the bounded agentic tool-use loop (plan item 8: multi-step calls
and the runaway-loop guard UC §13.5 promises)."""

from __future__ import annotations

from lab_agent.adapters.base import AdapterResponse, Message, ToolCall, ToolSpec
from lab_agent.evidence import EvidenceLedger, compute_source_id
from lab_agent.loop import run_tool_loop
from tests.fakes import FakeMCP


class _MultiStepAdapter:
    """Calls ``get_note`` on turns 1-2, then stops (no tool calls) on turn 3."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        response_schema: dict[str, object] | None = None,
        schema_name: str = "result",
    ) -> AdapterResponse:
        self.calls += 1
        if self.calls <= 2:
            note_id = f"n{self.calls}"
            return AdapterResponse(text="", tool_calls=[ToolCall(id=f"c{self.calls}", name="get_note", arguments={"note_id": note_id})])
        return AdapterResponse(text="grounded summary", tool_calls=[])


class _AlwaysWantsToolsAdapter:
    """Never stops requesting tools -- exercises the ``max_steps`` backstop."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        response_schema: dict[str, object] | None = None,
        schema_name: str = "result",
    ) -> AdapterResponse:
        self.calls += 1
        return AdapterResponse(
            text="", tool_calls=[ToolCall(id=f"c{self.calls}", name="get_note", arguments={"note_id": "n"})]
        )


async def test_run_tool_loop_drives_multiple_sequential_tool_calls():
    """Two distinct reads across two turns must both land in the ledger and
    the transcript, and the loop must stop once the model requests no more
    tools (turn 3) -- well short of any step cap."""
    mcp = FakeMCP(note_text={"n1": "first note", "n2": "second note"})
    adapter = _MultiStepAdapter()
    ledger = EvidenceLedger()
    messages: list[Message] = [{"role": "user", "content": "ground yourself"}]

    result = await run_tool_loop(adapter, mcp, messages, [], max_steps=10, ledger=ledger)  # type: ignore[arg-type]

    assert adapter.calls == 3  # two tool turns + one final no-tool turn
    tool_messages = [m for m in result if m["role"] == "tool"]
    assert len(tool_messages) == 2
    assert len(ledger.audit_summary()) == 2  # both successful reads captured


async def test_run_tool_loop_stops_at_max_steps_when_model_never_yields():
    """A model that always requests a tool call must not loop forever -- the
    ``max_steps`` cap is the runaway-loop guard, not just a soft target."""
    mcp = FakeMCP(note_text={"n": "text"})
    adapter = _AlwaysWantsToolsAdapter()
    ledger = EvidenceLedger()
    messages: list[Message] = [{"role": "user", "content": "ground yourself"}]

    await run_tool_loop(adapter, mcp, messages, [], max_steps=3, ledger=ledger)  # type: ignore[arg-type]

    assert adapter.calls == 3  # exactly the cap, never more
    tool_messages = [m for m in messages if m["role"] == "tool"]
    assert len(tool_messages) == 3  # every step's call still executed and recorded


async def test_run_tool_loop_ledger_ids_match_across_repeated_reads():
    """A repeated identical read across steps converges on the same ledger id
    rather than being treated as fresh evidence each time."""
    mcp = FakeMCP(note_text={"n": "same content"})

    class _RepeatSameCallAdapter:
        def __init__(self) -> None:
            self.calls = 0

        async def generate(
            self,
            messages: list[Message],
            tools: list[ToolSpec] | None = None,
            response_schema: dict[str, object] | None = None,
            schema_name: str = "result",
        ) -> AdapterResponse:
            self.calls += 1
            if self.calls <= 2:
                return AdapterResponse(
                    text="", tool_calls=[ToolCall(id=f"c{self.calls}", name="get_note", arguments={"note_id": "n"})]
                )
            return AdapterResponse(text="done", tool_calls=[])

    ledger = EvidenceLedger()
    messages: list[Message] = [{"role": "user", "content": "ground yourself"}]
    await run_tool_loop(_RepeatSameCallAdapter(), mcp, messages, [], max_steps=10, ledger=ledger)  # type: ignore[arg-type]

    import json

    note_json = json.dumps({"id": "n", "widget_type": "Note", "title": "", "text": "same content"})
    expected_id = compute_source_id("get_note", {"note_id": "n"}, note_json)
    assert ledger.known(expected_id)
    assert len(ledger.audit_summary()) == 1  # one distinct read, not two
