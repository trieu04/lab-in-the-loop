"""Tests for the artifact-service settings added to ``lab_agent.config.Settings``
in Phase 3: private-by-default bind host/port and the empty public-URL
placeholder (no token secret setting exists -- capability tokens are
per-artifact CSPRNG values hashed in the DB, never derived from config).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lab_agent.config import Settings


def test_artifact_settings_default_to_private_bind_and_empty_public_url() -> None:
    settings = Settings(_env_file=None, _env_prefix="__TEST_NO_ENV__")

    assert settings.artifact_bind_host == "127.0.0.1"
    assert settings.artifact_bind_port == 8600
    assert settings.artifact_public_base_url == ""


def test_artifact_settings_overridable_via_env(monkeypatch) -> None:
    monkeypatch.setenv("LAB_AGENT_ARTIFACT_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("LAB_AGENT_ARTIFACT_BIND_PORT", "9000")
    monkeypatch.setenv("LAB_AGENT_ARTIFACT_PUBLIC_BASE_URL", "https://lab.example.com")

    settings = Settings(_env_file=None)

    assert settings.artifact_bind_host == "0.0.0.0"
    assert settings.artifact_bind_port == 9000
    assert settings.artifact_public_base_url == "https://lab.example.com"


def test_artifact_bind_port_rejects_out_of_range_values() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, _env_prefix="__TEST_NO_ENV__", artifact_bind_port=0)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Settings(artifact_bind_port=70_000)  # type: ignore[call-arg]
