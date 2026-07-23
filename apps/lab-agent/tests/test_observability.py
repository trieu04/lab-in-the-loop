"""Tests for safe, bounded in-process observability primitives."""

from __future__ import annotations

import pytest

from lab_agent.observability import EventContext, InProcessMetrics, emit_event, safe_event


class RecordingLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **fields: object) -> None:
        self.calls.append((event, fields))


def test_structured_events_allowlist_fields_and_redact_content_and_secrets() -> None:
    context = EventContext("run-1", "tenant-1", "canvas-1", round=2, stage="generate")
    secret = "Bearer top-secret-token"
    content = "confidential scientific observation"
    logger = RecordingLogger()

    payload = safe_event(
        "workflow_finished",
        context,
        status="success",
        adapter="deterministic",
        error_class="TimeoutError",
        content=content,
        authorization=secret,
        raw_response=content,
    )
    emitted = emit_event(logger, "workflow_finished", context, status="success", content=content)

    assert payload == {
        "event": "workflow_finished",
        "run_id": "run-1",
        "tenant_id": "tenant-1",
        "canvas_id": "canvas-1",
        "round": 2,
        "stage": "generate",
        "status": "success",
        "adapter": "deterministic",
        "error_class": "TimeoutError",
    }
    assert emitted["event"] == "workflow_finished"
    assert logger.calls == [("workflow_finished", {"run_id": "run-1", "tenant_id": "tenant-1", "canvas_id": "canvas-1", "round": 2, "stage": "generate", "status": "success"})]
    assert secret not in repr(payload)
    assert content not in repr(payload)


def test_metrics_bound_series_and_exclude_unsafe_labels() -> None:
    ticks = iter((10.0, 11.25))
    metrics = InProcessMetrics(max_series=2, clock=lambda: next(ticks))
    context = EventContext("run-1", "tenant-1", "canvas-1")
    secret = "Bearer top-secret-token"

    metrics.increment("canvas_scans", context, status="success", content="private result")
    with metrics.timer("job_duration", context, status="success", credential=secret):
        pass
    metrics.increment("pending_triggers", context, status="success")

    assert metrics.counter_value("canvas_scans", context, status="success") == 1
    stats = metrics.timer_stats("job_duration", context, status="success")
    assert stats is not None
    assert stats.count == 1
    assert stats.total_seconds == 1.25
    assert metrics.dropped_series == 1
    assert secret not in repr(metrics.snapshot())

    with pytest.raises(ValueError, match="metric name"):
        metrics.increment("canvas scans", context)
    with pytest.raises(ValueError, match="positive"):
        metrics.increment("canvas_scans", context, amount=0)


def test_invalid_context_fields_are_rejected() -> None:
    with pytest.raises(ValueError, match="run_id"):
        EventContext("run content here", "tenant-1", "canvas-1")
    with pytest.raises(ValueError, match="round"):
        EventContext("run-1", "tenant-1", "canvas-1", round=-1)
    with pytest.raises(ValueError, match="round"):
        EventContext("run-1", "tenant-1", "canvas-1", round="one")  # type: ignore[arg-type]
