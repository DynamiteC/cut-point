"""Phase 3 gate: spawn mcp-clickhouse over stdio exactly as the agent does, list
tools, run SELECT 1, and run retention_curve.sql for demo_001 through the MCP
`run_query` tool.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REQUIRED_ENV = [
    "CLICKHOUSE_HOST",
    "CLICKHOUSE_PORT",
    "CLICKHOUSE_USER",
    "CLICKHOUSE_PASSWORD",
    "CLICKHOUSE_DATABASE",
    "CLICKHOUSE_SECURE",
    "CLICKHOUSE_VERIFY",
]


from agent.cutpoint_agent.mcp import _server_command

_agent_command, _agent_args = _server_command()

def build_server_params() -> StdioServerParameters:
    env = {k: os.environ[k] for k in REQUIRED_ENV if k in os.environ}
    return StdioServerParameters(
        # Ask the agent's own factory how it launches the server, rather than
        # hardcoding one. This gate claims to spawn the server "exactly as the
        # agent will"; it stopped doing so when the agent moved to the console
        # script installed from the lockfile, so it was proving the wrong path.
        command=_agent_command,
        args=_agent_args,
        env=env,
        cwd=str(REPO_ROOT),
    )


async def run_smoke_test() -> int:
    from ingest.errors import MissingCredentialError

    if not os.environ.get("CLICKHOUSE_HOST"):
        raise MissingCredentialError("CLICKHOUSE_HOST")

    server_params = build_server_params()

    async with stdio_client(server_params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        tools_result = await session.list_tools()
        tool_names = [t.name for t in tools_result.tools]
        print(f"tools available: {tool_names}")
        assert "run_query" in tool_names, "run_query tool not registered"

        print("\nrunning: SELECT 1")
        result = await session.call_tool("run_query", {"query": "SELECT 1"})
        print(f"result: {result.content}")

        retention_sql = (
            (REPO_ROOT / "sql" / "analysis" / "retention_curve.sql")
            .read_text()
            .format(trailer_id="demo_001")
        )
        print("\nrunning: retention_curve.sql for demo_001")
        result = await session.call_tool("run_query", {"query": retention_sql})
        text = result.content[0].text if result.content else ""
        print(f"first 300 chars of result:\n{text[:300]}")

    return 0


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    return asyncio.run(run_smoke_test())


if __name__ == "__main__":
    sys.exit(main())
