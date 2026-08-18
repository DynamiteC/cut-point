"""McpToolset factory for mcp-clickhouse -- the ONLY way the agent touches
ClickHouse (read-only by design, see TASK.md section 1 rule 3).
"""

from __future__ import annotations

import os

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

    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command="uv",
                args=["run", "--with", "mcp-clickhouse", "mcp-clickhouse"],
                env={k: os.environ[k] for k in REQUIRED_ENV_VARS if k in os.environ},
            ),
            timeout=120,
        ),
    )
