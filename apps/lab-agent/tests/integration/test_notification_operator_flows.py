"""Cross-boundary operator notification lifecycle tests."""

from __future__ import annotations

import pytest

from lab_agent.models.governance import StopReason, TerminalStopEvent
from lab_agent.notification_outbox import NotificationOutbox
from lab_agent.notification_smtp import SMTPConfiguration
from tests.integration.fake_notification_sink import FakeNotificationSink, FakeSinkBehavior

CANVAS = "canvas-operator"


@pytest.fixture
def smtp_config() -> SMTPConfiguration:
    return SMTPConfiguration(
        enabled=True, host="smtp.test", port=587,
        sender="admin@lab.test", recipients=("operator@lab.test",),
        sender_allowlist=("admin@lab.test",), recipient_allowlist=("operator@lab.test",),
    )


def _event(canvas: str, index: int = 0) -> TerminalStopEvent:
    return TerminalStopEvent(
        canvas_id=canvas, trigger_id=f"loop:{canvas}:{index}",
        predecessor_id=f"result:{index}", reason=StopReason.MAX_ROUNDS,
        round_index=index + 1, closure_id=f"closed:{canvas}:{index}",
    )


async def test_operator_drain_is_bounded_and_canvas_scoped(store, smtp_config) -> None:
    sink = FakeNotificationSink()
    outbox = NotificationOutbox(store, smtp_config, "operator", batch_size=2, sink=sink)
    for canvas in ("canvas-a", "canvas-b"):
        for index in range(3):
            assert outbox.enqueue_terminal(_event(canvas, index))

    assert outbox.drain().sent == 2
    assert outbox.drain().sent == 2
    assert outbox.drain().sent == 2
    assert len(sink.deliveries) == 6
    assert {item.canvas_id for item in sink.deliveries} == {"canvas-a", "canvas-b"}
    assert len(store.list_notification_records(canvas_id="canvas-a")) == 3


async def test_operator_reject_quarantines_and_targeted_reset_requeues(store, smtp_config) -> None:
    sink = FakeNotificationSink()
    outbox = NotificationOutbox(store, smtp_config, "operator", max_attempts=1, sink=sink)
    outbox.enqueue_terminal(_event(CANVAS))
    record = store.list_notification_records(canvas_id=CANVAS)[0]
    sink.queue_behavior(record.logical_key, FakeSinkBehavior.REJECTED)

    assert outbox.drain().quarantined == 1
    assert store.get_notification(record.logical_key).status.value == "quarantined"
    store.reset_quarantined_notification(record.logical_key)
    sink.queue_behavior(record.logical_key, FakeSinkBehavior.ACCEPT)
    assert outbox.drain().sent == 1


async def test_operator_ambiguous_delivery_waits_for_reconciliation(store, smtp_config, clock) -> None:
    sink = FakeNotificationSink()
    outbox = NotificationOutbox(
        store, smtp_config, "operator", reconciliation_seconds=10, sink=sink
    )
    outbox.enqueue_terminal(_event(CANVAS))
    record = store.list_notification_records(canvas_id=CANVAS)[0]
    sink.queue_behavior(record.logical_key, FakeSinkBehavior.AMBIGUOUS)

    assert outbox.drain().ambiguous == 1
    clock.advance(5)
    assert outbox.drain().total == 0
    clock.advance(6)
    assert outbox.drain().total == 0
    assert store.get_notification(record.logical_key).status.value == "quarantined"
    store.reset_quarantined_notification(record.logical_key)
    sink.queue_behavior(record.logical_key, FakeSinkBehavior.ACCEPT)
    assert outbox.drain().sent == 1


async def test_operator_metadata_is_redacted(store, smtp_config) -> None:
    sink = FakeNotificationSink()
    outbox = NotificationOutbox(store, smtp_config, "operator", sink=sink)
    outbox.enqueue_terminal(_event(CANVAS))
    assert outbox.drain().sent == 1
    delivery = sink.deliveries[0]
    assert delivery.canvas_id == CANVAS
    assert "smtp" not in repr(delivery).lower()
    assert "password" not in repr(delivery).lower()


__all__ = [
    "test_operator_drain_is_bounded_and_canvas_scoped",
    "test_operator_reject_quarantines_and_targeted_reset_requeues",
    "test_operator_ambiguous_delivery_waits_for_reconciliation",
    "test_operator_metadata_is_redacted",
]
