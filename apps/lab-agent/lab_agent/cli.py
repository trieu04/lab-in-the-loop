"""Command-line entrypoint for the Lab-in-the-Loop experiment-loop agent.

    lab-agent watch --canvas <id> [--provider openai|claude]   # poll forever
    lab-agent once  --canvas <id> [--provider openai|claude]   # one poll cycle

`watch` continuously drives the connector workflow: idea → setup → robot →
result, and runs any detected experiment loop (result → setup) until the model
decides to stop. `once` runs a single poll cycle (handy for testing / cron).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import structlog

from lab_agent.adapters.factory import get_adapter
from lab_agent.config import get_settings
from lab_agent.mcp_client import MCPClient
from lab_agent.watch import process_once, run_watch

log = structlog.get_logger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, force=True)
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lab-agent", description="Lab-in-the-Loop experiment agent.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("watch", "Poll the canvas forever and drive the workflow."),
                            ("once", "Run a single poll cycle.")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--canvas", required=True, help="Canvas id to work on.")
        p.add_argument("--provider", choices=["openai", "claude"], default=None,
                       help="Override the configured model provider.")
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.provider:
        settings.model_provider = args.provider
    adapter = get_adapter(settings)

    async with MCPClient(settings.mcp_url) as mcp:
        if args.command == "watch":
            await run_watch(mcp, adapter, settings, args.canvas)
            return 0
        counts = await process_once(mcp, adapter, settings, args.canvas, set())
        print(f"Processed: {counts['setups']} setup(s), {counts['runs']} run(s), {counts['loops']} loop(s).")
        return 0


def main() -> None:
    _configure_logging()
    args = _build_parser().parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
