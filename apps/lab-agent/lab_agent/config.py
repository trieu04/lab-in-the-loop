"""Runtime configuration for the Lab-in-the-Loop agent."""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from lab_agent.provider_endpoints import default_provider_endpoints


class Settings(BaseSettings):
    """Environment-backed settings for the agent."""

    model_config = SettingsConfigDict(
        env_prefix="LAB_AGENT_", env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
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
    model_max_output_tokens: int = Field(
        default=4096, ge=1, description="Hard provider output cap used for requests and reservations."
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

    # ── Durable harness (Phase 2: SQLite WAL ledger, single-host scope) ──
    state_db_path: str = Field(
        default=".state/lab_agent.db",
        description="Path to the durable SQLite ledger (WAL journal). Parent created on startup.",
    )
    canvas_lease_ttl_seconds: float = Field(
        default=180.0,
        gt=0,
        description="Single-writer canvas lease TTL, renewed once per process_once/watch cycle.",
    )
    attempt_lease_ttl_seconds: float = Field(
        default=600.0,
        gt=0,
        description=(
            "Per-trigger workflow_attempt lease TTL. Covers one trigger's full "
            "processing (a loop trigger may run several rounds before stopping)."
        ),
    )
    retry_base_seconds: float = Field(
        default=5.0,
        gt=0,
        description="Base delay for full-jitter exponential backoff on a failed attempt.",
    )
    retry_max_seconds: float = Field(
        default=300.0,
        gt=0,
        description="Ceiling for full-jitter exponential backoff on a failed attempt.",
    )
    max_attempts: int = Field(
        default=5,
        ge=1,
        description="Attempts before a trigger is quarantined instead of retried.",
    )

    # ── Artifact service (Phase 3: capability-protected ASGI server) ─
    artifact_bind_host: str = Field(
        default="127.0.0.1",
        description="Bind host for the artifact HTTP service. Private by default -- "
        "production ingress/TLS is a separate, explicit deployment concern.",
    )
    artifact_bind_port: int = Field(
        default=8600,
        ge=1,
        le=65535,
        description="Bind port for the artifact HTTP service.",
    )
    artifact_public_base_url: str = Field(
        default="",
        description="Public base URL Canvus clients use to reach the artifact service "
        "(e.g. 'https://lab.internal'). Empty until a deployment target is chosen; "
        "never used to construct auth -- capability tokens are opaque and hashed in the DB.",
    )

    # ── Governance: task-stage routing (Phase 5, NFR-LITL-003) ──────
    routing_table: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Stage -> ordered provider preference (JSON in env). A stage "
        "absent here falls back to `model_provider`; the first preferred provider "
        "that is both configured and locality-authorized wins. No provider branch "
        "ever lives in orchestration code -- routing is pure config.",
    )

    # ── Governance: data-locality allowlist (NFR-LITL-002, fail closed) ──
    provider_endpoints: dict[str, str] = Field(
        default_factory=default_provider_endpoints,
        description="Provider -> approved HTTPS endpoint. Empty, invalid, or unlisted is denied before dispatch.",
    )
    provider_data_classifications: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "openai": ["public", "internal"],
            "claude": ["public", "internal"],
        },
        description="Provider -> classifications it may receive. A restricted or "
        "unknown-classified source is denied unless the provider is explicitly "
        "listed for it; an unlisted provider receives nothing (default deny).",
    )

    # ── Governance: per-run/per-canvas budgets (NFR-LITL-009) ───────
    run_token_budget: int | None = Field(
        default=None, description="Max total tokens per loop run (None = unbounded)."
    )
    run_cost_budget_usd: float | None = Field(
        default=None, description="Max estimated USD per loop run (None = unbounded)."
    )
    canvas_token_budget: int | None = Field(
        default=None, description="Max total tokens per canvas across runs (None = unbounded)."
    )
    canvas_cost_budget_usd: float | None = Field(
        default=None, description="Max estimated USD per canvas across runs (None = unbounded)."
    )

    # ── Governance: pricing (versioned; estimates are labelled, never billed) ──
    pricing_version: str = Field(
        default="unset",
        description="Version tag stamped on every cost estimate for invoice reconciliation.",
    )
    model_pricing: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        description="Model id -> {'input_per_1k', 'output_per_1k'} (JSON in env). A model "
        "absent here has UNKNOWN pricing -- cost is surfaced as unavailable, never zero.",
    )

    # ── Governance: stop policy (FR-LITL-013) ───────────────────────
    wall_time_budget_seconds: float | None = Field(
        default=None, description="Max wall-clock seconds per loop run (None = unbounded)."
    )
    no_progress_rounds: int = Field(
        default=3, ge=1,
        description="Consecutive rounds with an identical result signature that trip the "
        "no-progress stop.",
    )
    model_call_max_attempts: int = Field(
        default=3, ge=1,
        description="Bounded dispatch attempts for one logical model call after known "
        "non-dispatched transient failures. Ambiguous outcomes are held for reconciliation.",
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return process-wide settings, constructed once from the environment."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


__all__ = ["Settings", "get_settings"]
