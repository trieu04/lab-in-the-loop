"""Runtime configuration for the Lab-in-the-Loop agent.

All environment variables carry the ``LAB_AGENT_`` prefix. A single settings
instance is built lazily and reused, mirroring the pattern in
``canvus_mcp.client.get_settings``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed settings for the agent."""

    model_config = SettingsConfigDict(
        env_prefix="LAB_AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── canvus-mcp endpoint ─────────────────────────────────────────
    mcp_url: str = Field(
        default="http://127.0.0.1:8931/mcp",
        description="Streamable-HTTP endpoint of the canvus-mcp server.",
    )

    # ── Model provider selection ────────────────────────────────────
    model_provider: Literal["openai", "claude"] = Field(
        default="openai",
        description="Which model adapter to use.",
    )

    # ── OpenAI (openai-compatible) ──────────────────────────────────
    openai_api_key: str = Field(default="", description="OpenAI API key.")
    openai_model: str = Field(default="gpt-4o-mini", description="OpenAI model id.")
    openai_base_url: str = Field(
        default="",
        description="Optional OpenAI-compatible base URL (Ollama/vLLM/Azure).",
    )

    # ── Anthropic / Claude ──────────────────────────────────────────
    anthropic_api_key: str = Field(default="", description="Anthropic API key.")
    anthropic_model: str = Field(
        default="claude-sonnet-4-5",
        description="Anthropic model id.",
    )

    # ── Agent runtime bounds ────────────────────────────────────────
    max_tool_steps: int = Field(
        default=8,
        ge=1,
        description="Hard cap on tool-use iterations per model turn (guards runaway loops).",
    )
    watch_poll_seconds: float = Field(
        default=30.0,
        gt=0,
        description="Seconds between polls in `watch` mode.",
    )
    loop_max_rounds: int = Field(
        default=25,
        ge=1,
        description=(
            "Safety backstop on experiment-loop rounds. The LLM is the real stop "
            "decision (UC step 3); this only prevents a runaway if it never stops."
        ),
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return process-wide settings, constructed once from the environment."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


__all__ = ["Settings", "get_settings"]
