# canvus-mcp

An MCP (Model Context Protocol) server that exposes Canvus operations as tools,
built on [`canvus-sdk`](./canvus-sdk) and reusing the RagCluster
connection-graph logic from `canvus-serving`.

It serves over **streamable HTTP** and talks to a single **pre-configured**
Canvus server (from `CANVUS_API_URL` / `CANVUS_API_KEY`).

## Tools

| Tool | Purpose |
|---|---|
| `list_canvases` | List every canvas on the pre-configured server. |
| `scan_server` | Scan all canvases and report which contain a RagCluster widget. |
| `get_note` | Read a Note widget's text and formatting. |
| `get_widget` | Read any widget by id (type, mime, hash, geometry). |
| `download_pdf` | Download a PDF widget's bytes to disk → `{path, mime, size, sha256}`. |
| `download_image` | Download an Image widget's bytes to disk. |
| `download_asset` | Download a raw asset by content hash to disk. |
| `create_note` | Create a Note widget. |
| `create_browser` | Create a Browser widget. |
| `create_image` | Create an Image widget by uploading a local file. |
| `create_connector` | Create a Connector (connection) widget between two widgets. |
| `check_widget_connections` | List all connectors touching any widget. |
| `check_ragcluster_connections` | Report inputs/outputs of RagCluster widget(s). |
| `scan_experiment_workflow` | Snapshot the experiment workflow: classified nodes, pending forward triggers, and detected loops. |
| `detect_experiment_loops` | Detect experiment loops (a `[EXP:Result] → [EXP:Setup]` back-edge) and resolve the participating widget ids. |

### What is a "RagCluster"?

An **Image widget whose `title` starts with the marker prefix**
(default `RAGCluster_`, configurable via `CANVUS_MCP_RAGCLUSTER_MARKER`).
"Connections" are `Connector` widgets whose `src`/`dst` endpoints reference
widget ids. `check_ragcluster_connections` builds an in-memory connector index
and reports, per cluster, the incoming feeders (e.g. PDFs/Notes) and outgoing
products (e.g. result Notes).

## Configuration

Copy `.env.example` to `.env` and fill in:

```bash
CANVUS_API_URL=https://your-canvus-server.example/api/v1
CANVUS_API_KEY=your-long-lived-api-token
CANVUS_VERIFY_SSL=true

CANVUS_MCP_OUTPUT_DIR=./downloads      # where downloads are written
CANVUS_MCP_HOST=127.0.0.1
CANVUS_MCP_PORT=8931
CANVUS_MCP_RAGCLUSTER_MARKER=RAGCluster_
```

Downloaded bytes are written under `CANVUS_MCP_OUTPUT_DIR`; the tools return the
file **path plus metadata**, never the bytes inline.

## Run

```bash
uv sync --extra dev
uv run canvus-mcp          # serves streamable-HTTP on CANVUS_MCP_HOST:PORT
```

The MCP endpoint is `http://<host>:<port>/mcp`. Point any streamable-HTTP MCP
client at it.

## Connect to Claude Code

The server speaks streamable HTTP, so register it with the `http` transport.

1. Start the server (leave it running in its own terminal):

   ```bash
   uv run canvus-mcp        # e.g. serves http://127.0.0.1:8931/mcp
   ```

2. Add it to Claude Code at **user scope**:

   ```bash
   claude mcp add --transport http -s user canvus http://127.0.0.1:8931/mcp
   ```

   Adjust the host/port to match your `.env`.

   > **Scope pitfall:** without `-s user`, the default scope is `local`,
   > which registers the server **only for the directory you run the command
   > from**. In every other project `claude mcp list` will say "No MCP
   > servers configured" and the tools won't exist. Use `-s user` (all your
   > projects) or `-s project` (shared with the repo via `.mcp.json`).

3. Verify and use — **from the directory where you'll run Claude Code**:

   ```bash
   claude mcp list           # should show: canvus  ✓ Connected
   ```

   Then start a **fresh** Claude Code session. Inside it, run `/mcp` to see
   the server and its tools, then just ask — e.g. "scan the Canvus server
   for RagCluster widgets" invokes `scan_server`. Tools appear namespaced as
   `mcp__canvus__<tool>`.

### Troubleshooting: `/mcp` shows the tools but calls fail

If `/mcp` reports `canvus · ✔ connected · N tools` yet every call returns
`Error: No such tool available: mcp__canvus__...`, the session's tool schema
was built before the server registered. **Fully quit and relaunch Claude
Code** — resuming the session (`--resume`/`--continue`) is not enough, the
tool registry is only rebuilt on a fresh start. Also confirm the server was
registered in a scope visible from your current directory (see the scope
pitfall above).

To remove it later: `claude mcp remove canvus`.

> **Note:** the Canvus credentials come from this server's own `.env`
> (`CANVUS_API_URL` / `CANVUS_API_KEY`) — Claude Code only needs the MCP URL.
> Prefer `-s user` if you don't want the server URL committed to the repo.

### Alternative: stdio

The HTTP transport requires the server to be already running. If you'd
rather have Claude Code launch and manage the process itself, register it
over stdio with the built-in `--stdio` flag:

```bash
claude mcp add -s user canvus -- \
  uv run --directory /abs/path/to/apps/canvus-mcp canvus-mcp --stdio
```

Claude Code then starts the server on demand; credentials still come from
this app's `.env` (picked up via `--directory`). The HTTP path above is the
simplest for a long-running shared server and is recommended.

## Test

```bash
uv run pytest -q
uv run ruff check canvus_mcp tests
```

## Layout

```
canvus_mcp/
├── config.py         # env-backed Settings
├── client.py         # shared async canvus-sdk Client lifecycle
├── ragcluster.py     # vendored connector-index + connection summary
├── downloads.py      # save-to-disk + mime/hash helper
├── server.py         # FastMCP instance + entrypoint
└── tools/
    ├── scan.py         # list_canvases, scan_server
    ├── content.py      # get_note, get_widget, download_*
    ├── widgets.py      # create_note/browser/image/connector
    └── connections.py  # check_widget_connections, check_ragcluster_connections
```
