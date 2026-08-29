"""McpToolset factory for mcp-clickhouse.

This was once the only way the agent touched ClickHouse. It is not any more.
The analyst (step 1) and the validator read the database directly through
ingest.clickhouse_client.get_readonly_client(), which pins readonly=1 at the
session level, because the numbers were taken off the model entirely.

What remains here is the narrator's tool access (step 4): the one place a model
still queries the database, and therefore the one place that needs a boundary
which is read-only by construction rather than by configuration.
"""

from __future__ import annotations

import os
import shutil

from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from mcp import StdioServerParameters

REQUIRED_ENV_VARS = (
    "CLICKHOUSE_HOST",
    "CLICKHOUSE_PORT",
    "CLICKHOUSE_USER",
    "CLICKHOUSE_PASSWORD",
    "CLICKHOUSE_DATABASE",
    "CLICKHOUSE_SECURE",
    "CLICKHOUSE_VERIFY",
)


def clickhouse_toolset() -> McpToolset:
    """Build the toolset. Deliberately does NOT check credentials.

    agent.py exports `root_agent` at module scope, because that is the symbol
    `adk web` and `adk run` discover. Raising here therefore made merely
    IMPORTING the agent package require a database host, which broke test
    collection anywhere without a .env -- CI most obviously.

    Validating configuration at import time is the wrong boundary. The check now
    lives in run_live(), where work actually starts, so a real run still fails
    loudly and immediately. `make preflight` checks it too, and the MCP server
    itself errors on first query if it is somehow missed.
    """
    command, args = _server_command()
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=command,
                args=args,
                env={k: os.environ[k] for k in REQUIRED_ENV_VARS if k in os.environ},
            ),
            timeout=120,
        ),
    )


def _server_command() -> tuple[str, list[str]]:
    """Prefer the mcp-clickhouse already installed from uv.lock.

    `uv run --with mcp-clickhouse mcp-clickhouse` resolved the server from PyPI
    on every session: it ignored the version resolved in uv.lock (0.4.1; pyproject declares a >=0.1.8 floor), made a `uv` binary
    and outbound PyPI access hard runtime requirements inside the request
    handler, and meant a new upstream release could change agent behaviour
    without a single line of this repo changing.

    mcp-clickhouse is a declared dependency, so the console script is on PATH in
    the venv and in the image. Fall back to the old invocation only if it is
    genuinely missing.
    """
    installed = shutil.which("mcp-clickhouse")
    if installed:
        return installed, []
    return "uv", ["run", "--with", "mcp-clickhouse", "mcp-clickhouse"]
