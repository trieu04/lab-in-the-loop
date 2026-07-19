"""Adversarial unit regressions for the model-result boundary."""

from __future__ import annotations

import json

import pytest

from lab_agent.result_safety import ResultLimits, is_unsafe_data, sanitize_result


@pytest.mark.parametrize(
    "payload",
    [
        {"meta": {"Authorization": "Bearer reader-secret"}},
        {"meta": {"bearerToken": "reader-secret"}},
        {"meta": {"privateKey": "reader-secret"}},
        {"meta": {"set-cookie": "session=reader-secret"}},
        {"meta": {"raw-error": "provider failure"}},
        {"meta": {"stackTrace": "trace material"}},
        {"meta": {"access_token": "reader-secret"}},
        {"meta": {"sessionToken": "reader-secret"}},
        {"meta": {"id_token": "reader-secret"}},
        {"meta": {"token": "reader-secret"}},
        {"url": "https://artifact.example/report?token=reader-secret"},
    ],
)
def test_sensitive_nested_keys_are_rejected_across_name_styles(payload: dict[str, object]) -> None:
    assert sanitize_result(json.dumps(payload), ResultLimits()) is None


@pytest.mark.parametrize(
    "value",
    [
        "Authorization: Bearer reader-secret",
        "Bearer reader-secret",
        "sessionToken=reader-secret",
        "Cookie: session=reader-secret",
        "accessToken=reader-secret",
        "api-key: reader-secret",
        "-----BEGIN PRIVATE KEY-----\nreader-secret",
        "MCP raw error: reader-secret",
        "https://artifact.example/report?token=reader-secret",
        "/private/cache/result.json",
    ],
)
def test_sensitive_scalars_are_rejected(value: str) -> None:
    assert sanitize_result(json.dumps({"text": value}), ResultLimits()) is None


def test_safe_note_and_chunk_text_remain_model_usable() -> None:
    payload = {"note": "Observed increased fluorescence after 30 minutes.", "chunks": [{"text": "safe evidence"}]}

    assert sanitize_result(json.dumps(payload), ResultLimits()) == payload


def test_malicious_tool_arguments_are_rejected_before_dispatch() -> None:
    assert is_unsafe_data({"canvas_id": "canvas-a", "request": {"cookie": "session=reader-secret"}})
    assert is_unsafe_data({"canvas_id": "https://artifact.example/?token=reader-secret"})
    assert not is_unsafe_data({"canvas_id": "canvas-a", "ordinal": 4})
