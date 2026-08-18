"""Shared pytest fixtures."""

import json
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(REPO_ROOT / ".env")


@pytest.fixture(scope="session")
def ground_truth() -> dict:
    path = REPO_ROOT / "data" / "ground_truth.json"
    if not path.exists():
        pytest.skip("data/ground_truth.json not found -- run make generate-data first")
    return json.loads(path.read_text())


@pytest.fixture(scope="session")
def clickhouse_client():
    if not os.environ.get("CLICKHOUSE_HOST"):
        pytest.skip("CLICKHOUSE_HOST not set -- see .env.example")
    import clickhouse_connect

    return clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"],
        port=int(os.environ.get("CLICKHOUSE_PORT", "8443")),
        username=os.environ.get("CLICKHOUSE_USER", "default"),
        password=os.environ.get("CLICKHOUSE_PASSWORD", ""),
        database=os.environ.get("CLICKHOUSE_DATABASE", "cutpoint"),
        secure=os.environ.get("CLICKHOUSE_SECURE", "true").lower() == "true",
        verify=os.environ.get("CLICKHOUSE_VERIFY", "true").lower() == "true",
        connect_timeout=10,
    )
