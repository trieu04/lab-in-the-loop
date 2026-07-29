#!/usr/bin/env python3
"""Idempotently attach one user-supplied idea to an existing RagCluster.

The command is read-only unless ``--apply`` is supplied. It never creates a
RagCluster asset or embeds credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_LAB_AGENT = _ROOT / "apps" / "lab-agent"
if str(_LAB_AGENT) not in sys.path:
    sys.path.insert(0, str(_LAB_AGENT))

from lab_agent.config import Settings  # noqa: E402
from lab_agent.demo_seed import SeedError, build_seed_title, seed_demo  # noqa: E402
from lab_agent.mcp_client import MCPClient  # noqa: E402
from lab_agent.runtime import (  # noqa: E402
    RuntimeContext,
    build_runtime_context,
    close_runtime_context,
    resolve_tenant_context,
)
from lab_agent.tenant import TenantContext  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canvas", required=True, help="Existing canvas id.")
    parser.add_argument("--ragcluster-widget-id", required=True, help="Existing RagCluster Image widget id.")
    parser.add_argument("--idea-text", required=True, help="Complete Note text containing the {idea: marker.")
    parser.add_argument("--idea-key", default="phase9-demo", help="Stable idempotency key.")
    parser.add_argument("--title", default=None, help="Optional Note title.")
    parser.add_argument("--x", type=float, default=0.0, help="Note x coordinate.")
    parser.add_argument("--y", type=float, default=0.0, help="Note y coordinate.")
    parser.add_argument("--apply", action="store_true", help="Perform the durable Note/Connector writes.")
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable result.")
    return parser


def _tenant_scope(settings: Settings, canvas_id: str) -> TenantContext | None:
    """Resolve the immutable configured scope before opening MCP or SQLite."""
    context = resolve_tenant_context(settings)
    if context is not None:
        context.require_canvas(canvas_id)
    return context


def _tenant_id(context: TenantContext | None) -> str:
    return context.tenant_id if context is not None else "default"


def _output(result: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return
    print(f"canvas={result['canvas_id']} action={result['action']} applied={result['applied']}")
    if result.get("idea_widget_id"):
        print(f"idea_widget_id={result['idea_widget_id']}")


async def _seed(
    args: argparse.Namespace, *, settings: Settings | None = None,
    tenant_context: TenantContext | None = None,
) -> dict[str, object]:
    settings = settings or Settings()
    context = tenant_context if tenant_context is not None else _tenant_scope(settings, args.canvas)
    if context is not None:
        context.require_canvas(args.canvas)
    tenant_id = _tenant_id(context)
    title = build_seed_title(args, tenant_id)
    runtime: RuntimeContext | None = None
    if args.apply:
        runtime = build_runtime_context(settings, tenant_context=context)
    try:
        async with MCPClient(
            settings.mcp_url, settings.mcp_bearer_token,
            result_max_bytes=settings.mcp_result_max_bytes,
            max_content_items=settings.mcp_result_max_items,
        ) as mcp:
            return await seed_demo(
                mcp, runtime.store if runtime is not None else None, args,
                tenant_id=tenant_id, title=title,
            )
    finally:
        if runtime is not None:
            close_runtime_context(runtime)


def _seed_lock_path(args: argparse.Namespace, tenant_id: str) -> Path:
    scope = f"{tenant_id}\0{args.canvas}\0{args.ragcluster_widget_id}\0{args.idea_key}"
    return Path(tempfile.gettempdir()) / f"lap-seed-{hashlib.sha256(scope.encode()).hexdigest()}.lock"


@contextmanager
def _seed_lock(args: argparse.Namespace, tenant_id: str) -> Iterator[None]:
    with _seed_lock_path(args, tenant_id).open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def main() -> int:
    args = _parser().parse_args()
    try:
        settings = Settings()
        context = _tenant_scope(settings, args.canvas)
        with _seed_lock(args, _tenant_id(context)):
            _output(asyncio.run(_seed(args, settings=settings, tenant_context=context)), args.json)
    except SeedError as exc:
        print(f"SEED FAILED: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("SEED CANCELLED", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
