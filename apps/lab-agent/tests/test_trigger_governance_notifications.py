"""Pre-loop governance closure notification reconciliation tests."""

from __future__ import annotations

from lab_agent.config import Settings
from lab_agent.models.governance import StopReason
from lab_agent.notification_smtp import SMTPConfiguration
from lab_agent.trigger_governance import close_trigger_with_reason
from tests.fakes import FakeMCP


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        _env_prefix="__TEST_NO_ENV__",
        artifact_public_base_url="https://lab.test",
        notification_smtp=SMTPConfiguration(
            enabled=True,
            host="smtp.test",
            port=587,
            sender="lab@lab.test",
            recipients=("operator@lab.test",),
            sender_allowlist=("lab@lab.test",),
            recipient_allowlist=("operator@lab.test",),
        ),
    )


async def test_pre_loop_enqueue_failure_reconciles_without_duplicate_closure(
    store, monkeypatch,
) -> None:
    settings = _settings()
    mcp = FakeMCP()
    original_enqueue = store.enqueue_notification

    def fail_enqueue(_envelope):
        raise RuntimeError("forced enqueue failure")

    monkeypatch.setattr(store, "enqueue_notification", fail_enqueue)
    first, first_ready = await close_trigger_with_reason(
        mcp, settings, store, canvas_id="canvas", predecessor_id="idea1",
        round_index=1, reason=StopReason.LOCALITY_DENIAL,
        trigger_id="idea_setup:idea1",
    )
    monkeypatch.setattr(store, "enqueue_notification", original_enqueue)
    second, second_ready = await close_trigger_with_reason(
        mcp, settings, store, canvas_id="canvas", predecessor_id="idea1",
        round_index=1, reason=StopReason.LOCALITY_DENIAL,
        trigger_id="idea_setup:idea1",
    )

    assert first == second and not first_ready and second_ready
    assert len(store.list_notification_records(canvas_id="canvas")) == 1
    stopped = [event for event in store.list_audit_events("canvas") if event.event == "loop_stopped"]
    assert len(stopped) == 1
