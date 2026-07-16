#!/usr/bin/env python3
"""Operator CLI: mirror-first migration of legacy generated Notes to Browser artifacts.

Default run is a **read-only dry-run** that inventories the legacy Setup/Result/
Closed *Note* widgets on one canvas and how each is wired -- it makes no
ArtifactStore/SQLite mutations and no MCP write calls. Passing ``--apply``
creates, for each legacy Note, a capability-protected Browser mirror backed by
the canonical ArtifactStore plus equivalent source->Browser / Browser->dest
connectors, reusing the durable write primitives so a rerun converges on one
artifact / Browser / connector each (and repairs a stale capability URL rather
than duplicating it).

This is **mirror-only**: the original Notes and their connectors are never
deleted, archived, or edited -- there is no destructive path in Phase 3, so no
confirmation prompt is needed or bypassed. Capability tokens/URLs are bearer
secrets and are never printed; output is ids/counts/status only.

    python scripts/migrate-generated-notes-to-browser-artifacts.py --canvas <id>
    python scripts/migrate-generated-notes-to-browser-artifacts.py --canvas <id> --apply [--json]

Settings/env (LAB_AGENT_*) provide the MCP URL, durable state DB, and public
base URL. Exit codes: 0 = ok; 1 = at least one apply item failed (rerun-safe:
completed items are preserved); 2 = aborted before writes (missing/malformed
public base URL); other startup failures print ``STARTUP FAILED``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Standalone-script bootstrap: make ``lab_agent`` importable without depending on
# the current working directory (resolve from this file, never from CWD).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_LAB_AGENT = _REPO_ROOT / "apps" / "lab-agent"
if str(_LAB_AGENT) not in sys.path:
    sys.path.insert(0, str(_LAB_AGENT))

from lab_agent import artifact_migration as migration  # noqa: E402
from lab_agent.config import get_settings  # noqa: E402
from lab_agent.mcp_client import MCPClient  # noqa: E402
from lab_agent.runtime import (  # noqa: E402
    RuntimeStartupError,
    build_runtime_context,
    close_runtime_context,
)

_APPLY_HELP = (
    "Create the Browser mirrors and connectors. Without it the run is a "
    "read-only dry-run (inventory only). Mirror-only: original Notes are never "
    "deleted, archived, or edited."
)


def build_parser() -> argparse.ArgumentParser:
    """Argument parser. Default is dry-run; ``--apply`` performs mirror-only writes."""
    parser = argparse.ArgumentParser(
        prog="migrate-generated-notes-to-browser-artifacts",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--canvas", required=True, help="Canvas id to migrate (required).")
    parser.add_argument("--apply", action="store_true", help=_APPLY_HELP)
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Emit a concise JSON summary (ids/counts/status only; no tokens/URLs).",
    )
    return parser


def summary_dict(summary: migration.MigrationSummary) -> dict[str, object]:
    """JSON-safe summary: ids/counts/status only -- never a token or capability URL."""
    return {
        "canvas_id": summary.canvas_id,
        "mode": "apply" if summary.applied else "dry-run",
        "total": len(summary.items),
        "failed": summary.failed,
        "items": [vars(item) for item in summary.items],
    }


def _print_text(summary: migration.MigrationSummary) -> None:
    mode = "apply" if summary.applied else "dry-run"
    print(f"canvas={summary.canvas_id} mode={mode} total={len(summary.items)} failed={summary.failed}")
    for item in summary.items:
        line = (
            f"  note={item.note_id} type={item.artifact_type} status={item.status} "
            f"mirror={item.widget_id or '-'} in={item.inbound} out={item.outbound} "
            f"connectors={item.connectors_mirrored}"
        )
        print(line + (f" error={item.error}" if item.error else ""))


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not args.apply:
        async with MCPClient(settings.mcp_url) as mcp:
            summary = await migration.run_migration(
                mcp, None, settings, canvas_id=args.canvas, apply=False
            )
    else:
        try:
            migration.require_public_base(settings.artifact_public_base_url)
        except migration.ArtifactUrlError as exc:
            print(f"ABORTED before writes: {exc}", file=sys.stderr)
            return 2
        try:
            ctx = build_runtime_context(settings)
        except RuntimeStartupError as exc:
            print(f"STARTUP FAILED: {exc}", file=sys.stderr)
            return 1
        try:
            async with MCPClient(settings.mcp_url) as mcp:
                summary = await migration.run_migration(
                    mcp, ctx.store, settings, canvas_id=args.canvas, apply=True
                )
        finally:
            close_runtime_context(ctx)
    if args.as_json:
        print(json.dumps(summary_dict(summary)))
    else:
        _print_text(summary)
    return 1 if summary.failed else 0


def main() -> None:
    raise SystemExit(asyncio.run(_run(build_parser().parse_args())))


if __name__ == "__main__":
    main()
