"""Provider endpoint defaults and override precedence stay safe and testable."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lab_agent.adapters import claude_adapter, openai_adapter
from lab_agent.adapters.factory import build_provider_adapter
from lab_agent.config import Settings
from lab_agent.policy import build_locality_policy


def build_isolated_settings(**values: object) -> Settings:
    """Build settings without dotenv or inherited runtime configuration."""
    return Settings(_env_file=None, _env_prefix="__TEST_NO_ENV__", **values)


def test_builtin_provider_endpoints_default_to_their_canonical_https_origins() -> None:
    settings = build_isolated_settings(openai_api_key="openai-key", anthropic_api_key="anthropic-key")

    assert settings.provider_endpoints == {
        "openai": "https://api.openai.com/v1",
        "claude": "https://api.anthropic.com",
    }
    locality = build_locality_policy(settings)
    assert locality.authorize("openai", []).allowed
    assert locality.authorize("claude", []).allowed


def test_no_key_configuration_has_no_implicit_provider_endpoint() -> None:
    settings = build_isolated_settings()

    assert settings.provider_endpoints == {}
    with pytest.raises(ValueError, match="HTTPS endpoint"):
        build_provider_adapter("openai", settings)


def test_explicit_endpoint_overrides_defaults_and_invalid_endpoint_is_denied(monkeypatch) -> None:
    captured: dict[str, str | int | None] = {}

    class RecordingOpenAIAdapter:
        def __init__(
            self, api_key: str, model: str, base_url: str | None = None, max_output_tokens: int = 4096
        ) -> None:
            captured["openai"] = base_url
            captured["openai_limit"] = max_output_tokens

    class RecordingClaudeAdapter:
        def __init__(
            self, api_key: str, model: str, base_url: str | None = None, max_output_tokens: int = 4096
        ) -> None:
            captured["claude"] = base_url
            captured["claude_limit"] = max_output_tokens

    monkeypatch.setattr(openai_adapter, "OpenAIAdapter", RecordingOpenAIAdapter)
    monkeypatch.setattr(claude_adapter, "ClaudeAdapter", RecordingClaudeAdapter)
    settings = build_isolated_settings(
        openai_api_key="openai-key",
        anthropic_api_key="anthropic-key",
        model_max_output_tokens=73,
        openai_base_url="https://legacy.example",
        provider_endpoints={"openai": "https://proxy.example", "claude": "https://claude.example"},
    )

    build_provider_adapter("openai", settings)
    build_provider_adapter("claude", settings)

    assert captured == {
        "openai": "https://proxy.example",
        "openai_limit": 73,
        "claude": "https://claude.example",
        "claude_limit": 73,
    }
    invalid = build_isolated_settings(
        openai_api_key="openai-key", provider_endpoints={"openai": "not-a-url"}
    )
    assert not build_locality_policy(invalid).authorize("openai", []).allowed
    assert not build_locality_policy(invalid).authorize("custom", []).allowed
    with pytest.raises(ValueError, match="HTTPS endpoint"):
        build_provider_adapter("openai", invalid)

    missing = build_isolated_settings(openai_api_key="openai-key", provider_endpoints={})
    with pytest.raises(ValueError, match="HTTPS endpoint"):
        build_provider_adapter("openai", missing)

    legacy_base_url = build_isolated_settings(
        openai_api_key="openai-key", openai_base_url="https://legacy.example"
    )
    build_provider_adapter("openai", legacy_base_url)
    assert captured["openai"] == "https://legacy.example"
    with pytest.raises(ValueError, match="HTTPS endpoint"):
        build_provider_adapter("custom", settings)


async def test_cli_builds_governed_context_with_legacy_canonical_endpoints(monkeypatch, tmp_path) -> None:
    from lab_agent import cli

    settings = build_isolated_settings(
        openai_api_key="openai-key", state_db_path=str(tmp_path / "state.db")
    )
    runtime = SimpleNamespace(store=object(), runtime_instance_id="worker")
    captured: dict[str, object] = {}

    class FakeMCP:
        async def __aenter__(self) -> FakeMCP:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    def get_adapters(received: Settings) -> dict[str, object]:
        captured["adapter_endpoints"] = received.provider_endpoints
        return {"openai": object()}

    def build_context(*args: object) -> object:
        captured["context_endpoints"] = settings.provider_endpoints
        return object()

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "build_runtime_context", lambda _: runtime)
    monkeypatch.setattr(cli, "close_runtime_context", lambda _: None)
    monkeypatch.setattr(cli, "get_adapters", get_adapters)
    monkeypatch.setattr(cli, "build_context", build_context)
    monkeypatch.setattr(cli, "GovernedAdapter", lambda context: context)
    monkeypatch.setattr(cli, "MCPClient", lambda _url, _token, **_limits: FakeMCP())
    monkeypatch.setattr(cli, "release_lease_with_audit", lambda *args: True)

    async def process_once(*args: object) -> dict[str, int]:
        return {"setups": 0, "runs": 0, "loops": 0}

    monkeypatch.setattr(cli, "process_once", process_once)
    result = await cli._run(SimpleNamespace(command="once", provider=None, canvas="canvas"))

    assert result == 0
    assert captured["adapter_endpoints"] == settings.provider_endpoints
    assert captured["context_endpoints"] == settings.provider_endpoints
