"""McpToolset factory for mcp-clickhouse -- the ONLY way the agent touches
ClickHouse (read-only by design, see TASK.md section 1 rule 3).
"""

from __future__ import annotations

import os
import shutil

from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from mcp import StdioServerParameters

from ingest.errors import MissingCredentialError

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
    if not os.environ.get("CLICKHOUSE_HOST"):
        raise MissingCredentialError("CLICKHOUSE_HOST")

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
    on every session: it ignored the pinned 0.4.1 in uv.lock, made a `uv` binary
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
