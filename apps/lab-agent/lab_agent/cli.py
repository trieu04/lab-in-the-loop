"""Command-line entrypoint for the Lab-in-the-Loop experiment-loop agent.

    lab-agent watch --canvas <id> [--provider openai|claude]   # poll forever
    lab-agent once  --canvas <id> [--provider openai|claude]   # one poll cycle
    lab-agent integrity                                        # verify DB + audit chain
    lab-agent list-quarantined [--canvas <id>]                 # show quarantined attempts
    lab-agent reset --canvas <id> --trigger <trigger_id>       # un-quarantine one attempt
    lab-agent retry-model-intent --canvas <id> --intent <id>   # retry one safe model failure
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
import json
import logging
import sys
import urllib.error
import urllib.request
from typing import TYPE_CHECKING
from urllib.parse import urljoin

import structlog
import uvicorn

from lab_agent import admin
from lab_agent.adapters.factory import get_adapters
from lab_agent.artifact_server import create_artifact_app
from lab_agent.artifact_store import ArtifactStore
from lab_agent.config import Settings, get_settings
from lab_agent.mcp_client import MCPClient
from lab_agent.model_gateway import GovernedAdapter, build_context
from lab_agent.runtime import (
    RuntimeContext,
    RuntimeStartupError,
    build_runtime_context,
    close_runtime_context,
    release_lease_with_audit,
    resolve_tenant_context,
)
from lab_agent.tenant import (
    TenantConfigurationError,
    TenantContext,
    require_single_credential_domain,
)
from lab_agent.watch import process_once, run_watch, run_watch_many

if TYPE_CHECKING:
    from lab_agent.model_gateway import GovernanceContext

log = structlog.get_logger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, force=True)
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _intent_id(value: str) -> str:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise argparse.ArgumentTypeError("intent must be a full 64-character lowercase hex id")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lab-agent", description="Lab-in-the-Loop experiment agent.")
    sub = parser.add_subparsers(dest="command", required=True)

    watch_parser = sub.add_parser("watch", help="Poll one or more allowlisted canvases forever.")
    watch_parser.add_argument("--canvas", default=None, help="Restrict the watcher to one canvas id.")
    watch_parser.add_argument("--canvases", nargs="+", default=None, help="Restrict the watcher to these canvas ids.")
    watch_parser.add_argument("--provider", choices=["openai", "claude"], default=None,
                              help="Override the configured model provider.")

    once_parser = sub.add_parser("once", help="Run a single poll cycle.")
    once_parser.add_argument("--canvas", required=True, help="Canvas id to work on.")
    once_parser.add_argument("--provider", choices=["openai", "claude"], default=None,
                             help="Override the configured model provider.")

    sub.add_parser("integrity", help="Verify SQLite integrity and the audit hash chain.")
    sub.add_parser("health", help="Report sanitized local and dependency readiness.")

    p = sub.add_parser("list-quarantined", help="List quarantined workflow attempts.")
    p.add_argument("--canvas", default=None, help="Restrict to one canvas id.")

    p = sub.add_parser("reset", help="Reset one quarantined attempt back to pending.")
    p.add_argument("--canvas", required=True, help="Canvas id of the attempt.")
    p.add_argument("--trigger", required=True, help="Trigger id of the attempt (e.g. idea_setup:<id>).")

    p = sub.add_parser("retry-model-intent", help="Retry one safe terminal model provider failure.")
    p.add_argument("--canvas", required=True, help="Canvas id of the model intent.")
    p.add_argument("--intent", required=True, type=_intent_id, help="Full model intent id.")

    p = sub.add_parser("backup", help="Write a consistent hot backup of the durable ledger.")
    p.add_argument("--to", required=True, dest="destination", help="Backup destination file path.")
    p.add_argument("--global-authority", action="store_true", help="Authorize a global backup explicitly.")

    p = sub.add_parser("notification-status", help="Show safe notification delivery counts.")
    p.add_argument("--canvas", default=None, help="Restrict to one canvas id.")
    p = sub.add_parser("list-notification-quarantined", help="List quarantined notification identifiers.")
    p.add_argument("--canvas", default=None, help="Restrict to one canvas id.")
    p = sub.add_parser("retry-notification", help="Retry one quarantined notification.")
    p.add_argument("--key", required=True, dest="logical_key", help="Notification logical key.")
    p = sub.add_parser("quarantine-notification", help="Quarantine one due notification.")
    p.add_argument("--key", required=True, dest="logical_key", help="Notification logical key.")

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
    app = create_artifact_app(
        ArtifactStore(ctx.store.conn, tenant_context=ctx.store.tenant_context),
        expected_scope=getattr(ctx, "tenant_context", ctx.store.tenant_context),
    )
    config = uvicorn.Config(app, host=host, port=port, access_log=False)
    server = uvicorn.Server(config)
    await server.serve()
    return 0


def _watch_canvases(settings: Settings, args: argparse.Namespace) -> list[str]:
    if args.canvas and args.canvases:
        raise TenantConfigurationError("use either --canvas or --canvases, not both")
    requested = [args.canvas] if args.canvas else list(args.canvases or settings.allowed_canvas_ids)
    if not requested:
        raise TenantConfigurationError("watch requires --canvas, --canvases, or LAB_AGENT_ALLOWED_CANVAS_IDS")
    if len(requested) != len(set(requested)):
        raise TenantConfigurationError("watch canvases must not contain duplicates")
    return requested


def _probe_artifact(base_url: str) -> str:
    try:
        with urllib.request.urlopen(urljoin(base_url.rstrip("/") + "/", "healthz"), timeout=2.0) as response:
            return "ok" if response.status == 200 else "degraded"
    except (OSError, urllib.error.URLError, ValueError):
        return "blocked"


async def _probe_mcp(settings: Settings) -> str:
    try:
        async with MCPClient(
            settings.mcp_url,
            settings.mcp_bearer_token,
            result_max_bytes=settings.mcp_result_max_bytes,
            max_content_items=settings.mcp_result_max_items,
        ) as mcp:
            return "ok" if await mcp.list_tools() else "degraded"
    except Exception:  # Third-party transport exceptions have no safe shared type.
        return "blocked"


async def _health(ctx: RuntimeContext, settings: Settings) -> int:
    dependencies = {
        "state_store": "ok",
        "audit_chain": "ok",
        "provider": "ok" if settings.provider_endpoints.get(settings.model_provider) else "not_configured",
        "artifact_service": "not_configured",
        "mcp": "blocked",
        "ingestion_worker": "not_checked",
        "phase8_adapters": "ok" if settings.phase8_execution_mode == "dry_run" else "blocked",
    }
    mcp_probe = _probe_mcp(settings)
    artifact_probe = (
        asyncio.to_thread(_probe_artifact, settings.artifact_public_base_url)
        if settings.artifact_public_base_url
        else None
    )
    if artifact_probe is None:
        dependencies["mcp"] = await mcp_probe
    else:
        dependencies["mcp"], dependencies["artifact_service"] = await asyncio.gather(
            mcp_probe, artifact_probe
        )
    overall = "blocked" if "blocked" in dependencies.values() else (
        "degraded" if any(status != "ok" for status in dependencies.values()) else "ok"
    )
    print(json.dumps({"status": overall, "dependencies": dependencies}, sort_keys=True))
    return 1 if overall == "blocked" else 0


async def _run(args: argparse.Namespace) -> int:
    try:
        settings = get_settings()
        canvases: list[str] = []
        requested_canvases: list[str] = []
        if args.command == "watch":
            canvases = _watch_canvases(settings, args)
            requested_canvases = canvases
        elif args.command == "once":
            canvases = [args.canvas]
            requested_canvases = canvases
        elif args.command in {
            "list-quarantined", "reset", "retry-model-intent", "notification-status",
            "list-notification-quarantined", "retry-notification", "quarantine-notification",
        }:
            requested_canvases = (
                [args.canvas]
                if getattr(args, "canvas", None)
                else list(settings.allowed_canvas_ids)
            )
            if not requested_canvases and settings.tenant_id != "default":
                raise TenantConfigurationError(
                    "tenant-scoped admin commands require LAB_AGENT_ALLOWED_CANVAS_IDS"
                )
        tenant: TenantContext | None = resolve_tenant_context(settings, requested_canvases)
        if tenant is not None:
            require_single_credential_domain([tenant])
        ctx = build_runtime_context(settings)
    except TenantConfigurationError as exc:
        print(f"STARTUP FAILED: {exc}", file=sys.stderr)
        return 1
    except RuntimeStartupError as exc:
        print(f"STARTUP FAILED: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print("STARTUP FAILED: invalid configuration.", file=sys.stderr)
        return 1

    try:
        if args.command == "integrity":
            return admin.check_integrity(ctx)
        if args.command == "list-quarantined":
            return admin.list_quarantined(ctx, args.canvas)
        if args.command == "reset":
            return admin.reset_attempt(ctx, args.canvas, args.trigger)
        if args.command == "retry-model-intent":
            return admin.retry_model_intent(ctx, args.canvas, args.intent)
        if args.command == "backup":
            return admin.backup(
                ctx, args.destination, global_authority=getattr(args, "global_authority", False)
            )
        if args.command == "notification-status":
            return admin.notification_status(ctx, args.canvas)
        if args.command == "list-notification-quarantined":
            return admin.list_notification_quarantined(ctx, args.canvas)
        if args.command == "retry-notification":
            return admin.retry_notification(ctx, args.logical_key)
        if args.command == "quarantine-notification":
            return admin.quarantine_notification(ctx, args.logical_key)
        if args.command == "serve-artifacts":
            return await _serve_artifacts(ctx, settings, args)
        if args.command == "health":
            return await _health(ctx, settings)

        if args.provider:
            settings.model_provider = args.provider
        adapters = get_adapters(settings)
        governance: dict[str, GovernanceContext] = {}

        def governance_for(canvas_id: str) -> GovernanceContext:
            context = governance.get(canvas_id)
            if context is None:
                context = build_context(ctx.store, settings, canvas_id, adapters)
                governance[canvas_id] = context
            return context

        def adapter_for(canvas_id: str) -> GovernedAdapter:
            return GovernedAdapter(governance_for(canvas_id))

        async with MCPClient(
            settings.mcp_url,
            settings.mcp_bearer_token,
            result_max_bytes=settings.mcp_result_max_bytes,
            max_content_items=settings.mcp_result_max_items,
        ) as mcp:
            if args.command == "watch":
                if len(canvases) == 1:
                    canvas_id = canvases[0]
                    gov = governance_for(canvas_id)
                    await run_watch(
                        mcp,
                        adapter_for(canvas_id),
                        settings,
                        ctx.store,
                        ctx.runtime_instance_id,
                        canvas_id,
                        gov=gov,
                        notifications=ctx.notifications,
                    )
                else:
                    await run_watch_many(
                        mcp,
                        adapter_for,
                        settings,
                        ctx.store,
                        ctx.runtime_instance_id,
                        canvases,
                        governance_factory=governance_for,
                        notifications=ctx.notifications,
                        tenant_id=tenant.tenant_id if tenant is not None else "default",
                        tenant_context=tenant,
                    )
                return 0
            canvas_id = canvases[0]
            gov = governance_for(canvas_id)
            try:
                counts = await process_once(
                    mcp,
                    adapter_for(canvas_id),
                    settings,
                    ctx.store,
                    ctx.runtime_instance_id,
                    canvas_id,
                    gov=gov,
                    notifications=ctx.notifications,
                )
            finally:
                release_lease_with_audit(ctx.store, ctx.runtime_instance_id, canvas_id)
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
