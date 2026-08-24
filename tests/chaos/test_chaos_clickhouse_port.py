"""Chaos scenario 2: Point CLICKHOUSE_HOST at a wrong port (network partition simulation).

Proves: MCP connection failure surfaces as a specific, actionable error
within the configured timeout, not a hang.
"""

from __future__ import annotations

import time

import pytest


@pytest.mark.timeout(20)
def test_wrong_clickhouse_port_gives_actionable_error(monkeypatch):
    """Connect to a port where nothing listens; expect a fast, descriptive failure."""
    # Use a port that is almost certainly unused
    monkeypatch.setenv("CLICKHOUSE_HOST", "127.0.0.1")
    monkeypatch.setenv("CLICKHOUSE_PORT", "19999")
    monkeypatch.setenv("CLICKHOUSE_USER", "default")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "")
    monkeypatch.setenv("CLICKHOUSE_DATABASE", "cutpoint")
    monkeypatch.setenv("CLICKHOUSE_SECURE", "false")
    monkeypatch.setenv("CLICKHOUSE_VERIFY", "false")

    from ingest.clickhouse_client import get_ingest_client

    start = time.time()
    with pytest.raises(Exception) as exc_info:
        client = get_ingest_client()
        # Force an actual connection attempt (get_client may be lazy)
        client.query("SELECT 1")

    elapsed = time.time() - start

    # Must not hang: should fail within 15 seconds
    assert elapsed < 15, f"Connection attempt took {elapsed:.1f}s, expected < 15s"

    # Error should be actionable: mention connection, host, port, or refused
    error_text = str(exc_info.value).lower()
    assert any(
        keyword in error_text
        for keyword in ["connect", "refused", "host", "port", "timeout", "connection"]
    ), f"Error not actionable: {exc_info.value}"
