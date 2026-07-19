"""Runtime configuration for the Lab-in-the-Loop agent."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from lab_agent.provider_endpoints import default_provider_endpoints


class Settings(BaseSettings):
    """Environment-backed settings for the agent."""

    model_config = SettingsConfigDict(
        env_prefix="LAB_AGENT_", env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # ── canvus-mcp endpoint and safe result boundary ──────────────────
    mcp_url: str = Field(default="http://127.0.0.1:8931/mcp")
    mcp_bearer_token: SecretStr | None = Field(default=None, repr=False)
    mcp_server_namespace: str = Field(default="canvus", pattern=r"^[A-Za-z0-9_-]+$", min_length=1, max_length=64)
    mcp_result_max_depth: int = Field(default=8, ge=1, le=16)
    mcp_result_max_containers: int = Field(default=256, ge=1, le=4096)
    mcp_result_max_string_chars: int = Field(default=8192, ge=1, le=32768)
    mcp_result_max_bytes: int = Field(default=32 * 1024, ge=1024, le=1024 * 1024)
    mcp_result_max_items: int = Field(default=256, ge=1, le=4096)
    mcp_argument_max_bytes: int = Field(default=32 * 1024, ge=1024, le=1024 * 1024)
    mcp_argument_max_items: int = Field(default=256, ge=1, le=4096)
    model_tool_calls_per_turn: int = Field(default=8, ge=1, le=64)
    model_tool_calls_per_run: int = Field(default=32, ge=1, le=512)
    model_transcript_max_bytes: int = Field(default=256 * 1024, ge=4096, le=4 * 1024 * 1024)
    model_evidence_max_bytes: int = Field(default=128 * 1024, ge=1024, le=4 * 1024 * 1024)
    model_evidence_max_items: int = Field(default=64, ge=1, le=4096)

    @field_validator("mcp_bearer_token", mode="before")
    @classmethod
    def blank_mcp_bearer_token_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def mcp_result_bounds_are_consistent(self) -> Settings:
        if self.mcp_result_max_string_chars > self.mcp_result_max_bytes:
            raise ValueError("mcp_result_max_string_chars must not exceed mcp_result_max_bytes")
        if self.model_tool_calls_per_turn > self.model_tool_calls_per_run:
            raise ValueError("model_tool_calls_per_turn must not exceed model_tool_calls_per_run")
        return self

    # ── Model provider selection ────────────────────────────────────
    model_provider: Literal["openai", "claude"] = Field(default="openai")

    # ── OpenAI (openai-compatible) ──────────────────────────────────
    openai_api_key: str = Field(default="")
    openai_model: str = Field(default="gpt-4o-mini")
    openai_base_url: str = Field(default="")

    # ── Anthropic / Claude ──────────────────────────────────────────
    anthropic_api_key: str = Field(default="")
    anthropic_model: str = Field(default="claude-sonnet-4-5")

    # ── Agent runtime bounds ────────────────────────────────────────
    max_tool_steps: int = Field(default=8, ge=1)
    model_max_output_tokens: int = Field(default=4096, ge=1)
    watch_poll_seconds: float = Field(default=30.0, gt=0)
    loop_max_rounds: int = Field(default=25, ge=1)

    # ── Durable harness (Phase 2: SQLite WAL ledger, single-host scope) ──
    state_db_path: str = Field(default=".state/lab_agent.db")
    canvas_lease_ttl_seconds: float = Field(default=180.0, gt=0)
    attempt_lease_ttl_seconds: float = Field(default=600.0, gt=0)
    retry_base_seconds: float = Field(default=5.0, gt=0)
    retry_max_seconds: float = Field(default=300.0, gt=0)
    max_attempts: int = Field(default=5, ge=1)

    # ── Artifact service (Phase 3: capability-protected ASGI server) ─
    artifact_bind_host: str = Field(default="127.0.0.1")
    artifact_bind_port: int = Field(default=8600, ge=1, le=65535)
    artifact_public_base_url: str = Field(default="")

    # ── Governance: task-stage routing (Phase 5, NFR-LITL-003) ──────
    routing_table: dict[str, list[str]] = Field(default_factory=dict)

    # ── Governance: data-locality allowlist (NFR-LITL-002, fail closed) ──
    provider_endpoints: dict[str, str] = Field(default_factory=default_provider_endpoints)
    provider_data_classifications: dict[str, list[str]] = Field(
        default_factory=lambda: {"openai": ["public", "internal"], "claude": ["public", "internal"]}
    )

    # ── Governance: per-run/per-canvas budgets (NFR-LITL-009) ───────
    run_token_budget: int | None = Field(default=None)
    run_cost_budget_usd: float | None = Field(default=None)
    canvas_token_budget: int | None = Field(default=None)
    canvas_cost_budget_usd: float | None = Field(default=None)

    # ── Governance: pricing (versioned; estimates are labelled, never billed) ──
    pricing_version: str = Field(default="unset")
    model_pricing: dict[str, dict[str, float]] = Field(default_factory=dict)

    # ── Governance: stop policy (FR-LITL-013) ───────────────────────
    wall_time_budget_seconds: float | None = Field(default=None)
    no_progress_rounds: int = Field(default=3, ge=1)
    model_call_max_attempts: int = Field(default=3, ge=1)


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return process-wide settings, constructed once from the environment."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


__all__ = ["Settings", "get_settings"]
