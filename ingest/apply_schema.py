"""Apply sql/001_schema.sql and sql/002_materialized_views.sql to ClickHouse.

Idempotent: every DDL statement uses IF NOT EXISTS, safe to re-run.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
SQL_FILES = ["sql/001_schema.sql", "sql/002_materialized_views.sql"]


def split_statements(sql_text: str) -> list[str]:
    statements = [s.strip() for s in sql_text.split(";")]
    return [s for s in statements if s]


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")

    import clickhouse_connect

    from ingest.errors import MissingCredentialError

    if not os.environ.get("CLICKHOUSE_HOST"):
        raise MissingCredentialError("CLICKHOUSE_HOST")

    client = clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"],
        port=int(os.environ.get("CLICKHOUSE_PORT", "8443")),
        username=os.environ.get("CLICKHOUSE_USER", "default"),
        password=os.environ.get("CLICKHOUSE_PASSWORD", ""),
        secure=os.environ.get("CLICKHOUSE_SECURE", "true").lower() == "true",
        verify=os.environ.get("CLICKHOUSE_VERIFY", "true").lower() == "true",
        connect_timeout=10,
    )

    for rel_path in SQL_FILES:
        sql_text = (REPO_ROOT / rel_path).read_text()
        # strip -- comments so they don't confuse the splitter on embedded semicolons
        sql_text = re.sub(r"--.*", "", sql_text)
        for statement in split_statements(sql_text):
            print(f"applying: {statement.splitlines()[0][:80]}...")
            client.command(statement)

    print("schema applied successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
