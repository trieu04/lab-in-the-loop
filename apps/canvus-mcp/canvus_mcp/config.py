"""Runtime configuration for the Canvus MCP server.

Connection settings are shared with the Canvus SDK (``CANVUS_API_URL`` /
``CANVUS_API_KEY``); MCP-specific settings use the ``CANVUS_MCP_`` prefix.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_CLASSIFICATION = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class Settings(BaseSettings):
    """Environment-backed settings for the MCP server."""

    model_config = SettingsConfigDict(
        env_prefix="CANVUS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Canvus SDK connection (the pre-configured server) ───────────
    api_url: str = Field(..., description="Base URL of the Canvus server.")
    api_key: str = Field(..., description="Long-lived Canvus API token.")
    verify_ssl: bool = Field(
        default=True,
        description="Verify the Canvus server's TLS certificate.",
    )

    # ── MCP server runtime (CANVUS_MCP_* in the environment) ────────
    mcp_output_dir: str = Field(
        default="./downloads",
        description="Directory where downloaded content is written.",
    )
    mcp_host: str = Field(
        default="127.0.0.1",
        description="Bind address for the streamable-HTTP transport.",
    )
    mcp_port: int = Field(
        default=8931,
        description="Bind port for the streamable-HTTP transport.",
    )
    mcp_ragcluster_marker: str = Field(
        default="RAGCluster_",
        description="Title prefix that identifies a RagCluster Image widget.",
    )
    mcp_ingestion_db_path: str = Field(default="./.state/ingestion.db")
    mcp_ingestion_cache_dir: str = Field(default="./.state/ingestion-cache")
    mcp_ingestion_worker_concurrency: int = Field(default=2, ge=1, le=4)
    mcp_ingestion_lease_seconds: float = Field(default=60.0, ge=0.1)
    mcp_ingestion_max_attempts: int = Field(default=3, ge=1, le=10)
    mcp_ingestion_max_source_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    mcp_ingestion_max_output_chars: int = Field(default=16_000, ge=1)
    mcp_ingestion_max_records: int = Field(default=500, ge=1)
    mcp_ingestion_max_pdf_pages: int = Field(default=200, ge=1)
    mcp_ingestion_pdf_password_file: str | None = Field(default=None)
    mcp_ingestion_chunk_char_cap: int = Field(default=8000, ge=1, le=8000)

    # Static local authorization. Tokens are write-only in config repr/logs.
    mcp_reader_token: SecretStr | None = Field(default=None)
    mcp_trusted_service_token: SecretStr | None = Field(default=None)
    mcp_operator_token: SecretStr | None = Field(default=None)
    mcp_reader_canvases: list[str] = Field(default_factory=list)
    mcp_trusted_service_canvases: list[str] = Field(default_factory=list)
    mcp_operator_canvases: list[str] = Field(default_factory=list)
    mcp_stdio_role: str = Field(default="reader", pattern="^(reader|trusted_service|operator)$")
    mcp_stdio_canvases: list[str] = Field(default_factory=list)
    canvas_classifications: Annotated[dict[str, str], NoDecode] = Field(default_factory=dict)

    @field_validator("canvas_classifications", mode="before")
    @classmethod
    def parse_canvas_classifications(cls, value: Any) -> dict[str, str]:
        """Treat malformed ownership classification configuration as unknown."""
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return {}
        if not isinstance(value, dict):
            return {}
        return {
            str(canvas): classification
            for canvas, classification in value.items()
            if isinstance(canvas, str)
            and isinstance(classification, str)
            and _CLASSIFICATION.fullmatch(classification) is not None
        }

    # ── Experiment-loop workflow markers ────────────────────────────
    mcp_robot_marker: str = Field(
        default="Robot_",
        description="Title prefix that identifies a Robot widget.",
    )
    mcp_exp_setup_marker: str = Field(
        default="[EXP:Setup",
        description="Title prefix of an experiment-setup Note.",
    )
    mcp_exp_result_marker: str = Field(
        default="[EXP:Result",
        description="Title prefix of an experiment-result Note.",
    )
    mcp_idea_marker: str = Field(
        default="{idea:",
        description="In-text marker of an experiment-idea Note.",
    )
    mcp_exp_closed_marker: str = Field(
        default="[EXP:Closed]",
        description="Title prefix of a terminal experiment-closed Note.",
    )
    mcp_exp_needs_input_marker: str = Field(
        default="[EXP:Needs Input]",
        description="Title prefix of a generated needs-input Note/Browser widget.",
    )


__all__ = ["Settings"]
