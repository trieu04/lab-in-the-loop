"""Runtime configuration for the Canvus MCP server.

Connection settings are shared with the Canvus SDK (``CANVUS_API_URL`` /
``CANVUS_API_KEY``); MCP-specific settings use the ``CANVUS_MCP_`` prefix.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
