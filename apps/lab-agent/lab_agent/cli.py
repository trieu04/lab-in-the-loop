"""Command-line entrypoint for the Lab-in-the-Loop experiment-loop agent.

    lab-agent watch --canvas <id> [--provider openai|claude]   # poll forever
    lab-agent once  --canvas <id> [--provider openai|claude]   # one poll cycle
    lab-agent integrity                                        # verify DB + audit chain
    lab-agent list-quarantined [--canvas <id>]                 # show quarantined attempts
    lab-agent reset --canvas <id> --trigger <trigger_id>       # un-quarantine one attempt
    lab-agent backup --to <path>                               # consistent hot backup
    lab-agent serve-artifacts [--host <host>] [--port <port>]  # run the artifact HTTP service

`watch` continuously drives the connector workflow: idea → setup → robot →
result, and runs any detected experiment loop (result → setup) until the model
decides to stop. `once` runs a single poll cycle (handy for testing / cron).

Every command opens the durable SQLite ledger first and fails closed (clear
exit code, no canvas work attempted) if it is missing, un-migratable, or its
integrity/audit chain does not check out -- see ``lab_agent.runtime``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import structlog
import uvicorn

from lab_agent import admin
from lab_agent.adapters.factory import get_adapter
from lab_agent.artifact_server import create_artifact_app
from lab_agent.artifact_store import ArtifactStore
from lab_agent.config import Settings, get_settings
from lab_agent.mcp_client import MCPClient
from lab_agent.runtime import (
    RuntimeContext,
    RuntimeStartupError,
    build_runtime_context,
    close_runtime_context,
    release_lease_with_audit,
)
from lab_agent.watch import process_once, run_watch

log = structlog.get_logger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, force=True)
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lab-agent", description="Lab-in-the-Loop experiment agent.")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("watch", "Poll the canvas forever and drive the workflow."),
        ("once", "Run a single poll cycle."),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--canvas", required=True, help="Canvas id to work on.")
        p.add_argument("--provider", choices=["openai", "claude"], default=None,
                       help="Override the configured model provider.")

    sub.add_parser("integrity", help="Verify SQLite integrity and the audit hash chain.")

    p = sub.add_parser("list-quarantined", help="List quarantined workflow attempts.")
    p.add_argument("--canvas", default=None, help="Restrict to one canvas id.")

    p = sub.add_parser("reset", help="Reset one quarantined attempt back to pending.")
    p.add_argument("--canvas", required=True, help="Canvas id of the attempt.")
    p.add_argument("--trigger", required=True, help="Trigger id of the attempt (e.g. idea_setup:<id>).")

    p = sub.add_parser("backup", help="Write a consistent hot backup of the durable ledger.")
    p.add_argument("--to", required=True, dest="destination", help="Backup destination file path.")

    p = sub.add_parser("serve-artifacts", help="Run the capability-protected artifact HTTP service.")
    p.add_argument("--host", default=None, help="Override the configured artifact bind host.")
    p.add_argument("--port", type=_port, default=None, help="Override the configured artifact bind port.")

    return parser


async def _serve_artifacts(ctx: RuntimeContext, settings: Settings, args: argparse.Namespace) -> int:
    """Run the artifact ASGI service until shutdown (Ctrl-C/SIGTERM).

    Uses ``uvicorn.Config``/``uvicorn.Server`` directly rather than
    ``uvicorn.run()`` because this coroutine already runs inside ``_run``'s
    own event loop (``asyncio.run`` in ``main()``) -- ``uvicorn.run()`` would
    try to start a second one. Access logging is disabled: capability URLs
    carry their bearer token in the query string, and an access log line
    would otherwise leak it to disk/stderr.
    """
    host = args.host or settings.artifact_bind_host
    port = args.port if args.port is not None else settings.artifact_bind_port
    app = create_artifact_app(ArtifactStore(ctx.store.conn))
    config = uvicorn.Config(app, host=host, port=port, access_log=False)
    server = uvicorn.Server(config)
    await server.serve()
    return 0


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    try:
        ctx = build_runtime_context(settings)
    except RuntimeStartupError as exc:
        print(f"STARTUP FAILED: {exc}", file=sys.stderr)
        return 1

    try:
        if args.command == "integrity":
            return admin.check_integrity(ctx)
        if args.command == "list-quarantined":
            return admin.list_quarantined(ctx, args.canvas)
        if args.command == "reset":
            return admin.reset_attempt(ctx, args.canvas, args.trigger)
        if args.command == "backup":
            return admin.backup(ctx, args.destination)
        if args.command == "serve-artifacts":
            return await _serve_artifacts(ctx, settings, args)

        if args.provider:
            settings.model_provider = args.provider
        adapter = get_adapter(settings)
        async with MCPClient(settings.mcp_url) as mcp:
            if args.command == "watch":
                # `run_watch` releases the lease itself in its own `finally`
                # (covers both a clean loop exit and Ctrl-C/KeyboardInterrupt).
                await run_watch(mcp, adapter, settings, ctx.store, ctx.runtime_instance_id, args.canvas)
                return 0
            try:
                counts = await process_once(
                    mcp, adapter, settings, ctx.store, ctx.runtime_instance_id, args.canvas
                )
            finally:
                # `once` is a single cycle followed by a clean shutdown: release
                # whatever canvas lease this process is holding right now.
                release_lease_with_audit(ctx.store, ctx.runtime_instance_id, args.canvas)
            print(f"Processed: {counts['setups']} setup(s), {counts['runs']} run(s), {counts['loops']} loop(s).")
            return 0
    finally:
        close_runtime_context(ctx)


def main() -> None:
    _configure_logging()
    args = _build_parser().parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
