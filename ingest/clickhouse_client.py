"""ClickHouse client factory for the ingest (write) path.

Per TASK.md section 1 rule 3: clickhouse-connect is permitted ONLY here and in
schema-apply / verification scripts -- never in the agent's query path.
"""

from __future__ import annotations

import os

import clickhouse_connect
from clickhouse_connect.driver.client import Client

from ingest.errors import MissingCredentialError

REQUIRED_VARS = ["CLICKHOUSE_HOST", "CLICKHOUSE_USER", "CLICKHOUSE_DATABASE"]


def get_ingest_client(database: str | None = None) -> Client:
    for var in REQUIRED_VARS:
        if not os.environ.get(var):
            raise MissingCredentialError(var)

    return clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"],
        port=int(os.environ.get("CLICKHOUSE_PORT", "8443")),
        username=os.environ["CLICKHOUSE_USER"],
        password=os.environ.get("CLICKHOUSE_PASSWORD", ""),
        database=database or os.environ["CLICKHOUSE_DATABASE"],
        secure=os.environ.get("CLICKHOUSE_SECURE", "true").lower() == "true",
        verify=os.environ.get("CLICKHOUSE_VERIFY", "true").lower() == "true",
        connect_timeout=10,
    )
