"""Configuration contracts for runtime extensions and governed pricing."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from lab_agent.config import Settings


def test_runtime_extension_defaults_are_safe() -> None:
    settings = Settings(_env_file=None, _env_prefix="__TEST_NO_ENV__")

    assert settings.in_silico_validation_enabled is True
    assert settings.wet_lab_execution_enabled is False
    assert settings.phase8_execution_enabled is False
    assert settings.phase8_execution_mode == "dry_run"


@pytest.mark.parametrize(("raw", "expected"), [("false", False), ("true", True)])
def test_in_silico_validation_flag_parses_environment(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    monkeypatch.setenv("LAB_AGENT_IN_SILICO_VALIDATION_ENABLED", raw)

    settings = Settings(_env_file=None)

    assert settings.in_silico_validation_enabled is expected


def test_model_pricing_accepts_zero_and_positive_rates() -> None:
    pricing = {
        "local-free": {"input_per_1k": 0.0, "output_per_1k": 0.0},
        "hosted": {"input_per_1k": 0.25, "output_per_1k": 1.0},
    }

    assert Settings(_env_file=None, _env_prefix="__TEST_NO_ENV__", model_pricing=pricing).model_pricing == pricing


@pytest.mark.parametrize(
    "pricing",
    [
        {"model": {"input_per_1k": 1.0}},
        {"model": {"output_per_1k": 1.0}},
        {"model": {"input_per_1k": -0.01, "output_per_1k": 1.0}},
        {"model": {"input_per_1k": 1.0, "output_per_1k": float("nan")}},
        {"model": {"input_per_1k": float("inf"), "output_per_1k": 1.0}},
        {"model": {"input_per_1k": "not-a-rate", "output_per_1k": 1.0}},
        {"model": []},
    ],
)
def test_model_pricing_rejects_incomplete_or_unsafe_rates(pricing: object) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, _env_prefix="__TEST_NO_ENV__", model_pricing=pricing)  # type: ignore[arg-type]


def test_empty_model_pricing_remains_a_valid_fail_closed_template() -> None:
    settings = Settings(_env_file=None, _env_prefix="__TEST_NO_ENV__", model_pricing={})

    assert settings.model_pricing == {}
    assert settings.pricing_version == "unset"


def test_env_example_documents_required_gates_and_optional_extensions() -> None:
    env_example = (Path(__file__).parents[1] / ".env.example").read_text(encoding="utf-8")

    expected_lines = {
        "LAB_AGENT_IN_SILICO_VALIDATION_ENABLED=true",
        "LAB_AGENT_WET_LAB_EXECUTION_ENABLED=false",
        "LAB_AGENT_PHASE8_EXECUTION_ENABLED=false",
        "LAB_AGENT_PHASE8_EXECUTION_MODE=dry_run",
        "LAB_AGENT_NOTIFICATION_SMTP__ENABLED=false",
        "LAB_AGENT_MODEL_PRICING={}",
        "LAB_AGENT_PRICING_VERSION=unset",
        "LAB_AGENT_TENANT_ID=default",
        "LAB_AGENT_ALLOWED_CANVAS_IDS=[]",
    }
    for line in expected_lines:
        assert line in env_example

    assert "intentionally blocks governed model dispatch" in env_example
    assert "no governance bypass" in env_example.lower()
