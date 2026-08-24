"""ClickHouse ingest load test: measures insert throughput and query latency.

Runnable standalone: uv run python tests/load/ingest_load.py

Produces tests/load/results/ingest_results.json with p50/p95/p99 latencies.
"""

from __future__ import annotations

import json
import random
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(REPO_ROOT / ".env")


RESULTS_DIR = Path(__file__).resolve().parent / "results"
SQL_DIR = REPO_ROOT / "sql" / "analysis"

BATCH_SIZES = [10_000, 50_000, 100_000]
ITERATIONS_PER_BATCH = 5
QUERY_ITERATIONS = 20
TRAILERS = ["demo_001", "demo_002", "demo_003"]

COHORTS = ["13-17", "18-24", "25-34", "35-44", "45+"]
REGIONS = ["us", "eu", "apac", "latam", "mea"]
DEVICES = ["mobile", "desktop", "tv"]
EVENT_TYPES = ["start", "heartbeat", "exit", "complete"]

# Load test table name (prefixed to avoid polluting real data)
LOADTEST_TABLE = "_loadtest_raw_playback_events"


def get_client():
    """Create ClickHouse client from .env config."""
    import clickhouse_connect
    import os

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


def generate_batch(n: int, rng: random.Random) -> list[tuple]:
    """Generate n synthetic rows matching raw_playback_events schema."""
    base_ts = datetime(2023, 11, 15, tzinfo=UTC)
    rows = []
    for _ in range(n):
        rows.append((
            base_ts,
            rng.choice(TRAILERS),
            uuid.uuid4(),
            rng.choice(COHORTS),
            rng.choice(REGIONS),
            rng.choice(DEVICES),
            rng.randint(0, 120),
            rng.choice(EVENT_TYPES),
        ))
    return rows


def create_loadtest_table(client) -> None:
    """Create a dedicated load-test table (not temporary, for visibility)."""
    client.command(f"DROP TABLE IF EXISTS {LOADTEST_TABLE}")
    client.command(f"""
        CREATE TABLE {LOADTEST_TABLE} (
            event_ts        DateTime64(3, 'UTC'),
            trailer_id      LowCardinality(String),
            session_id      UUID,
            cohort          LowCardinality(String),
            region          LowCardinality(String),
            device          LowCardinality(String),
            second_offset   UInt16,
            event_type      Enum8('start'=1,'heartbeat'=2,'exit'=3,'complete'=4)
        ) ENGINE = MergeTree
        ORDER BY (trailer_id, cohort, second_offset, event_ts)
    """)


def cleanup_loadtest_table(client) -> None:
    """Drop the load-test table."""
    client.command(f"DROP TABLE IF EXISTS {LOADTEST_TABLE}")


def benchmark_inserts(client) -> list[dict]:
    """Benchmark insert throughput for various batch sizes."""
    column_names = [
        "event_ts", "trailer_id", "session_id", "cohort",
        "region", "device", "second_offset", "event_type",
    ]
    rng = random.Random(42)
    results = []

    for batch_size in BATCH_SIZES:
        latencies = []
        print(f"  Benchmarking inserts: batch_size={batch_size}, iterations={ITERATIONS_PER_BATCH}")

        for i in range(ITERATIONS_PER_BATCH):
            batch = generate_batch(batch_size, rng)

            # Truncate between iterations to keep table small
            client.command(f"TRUNCATE TABLE {LOADTEST_TABLE}")

            start = time.perf_counter()
            client.insert(LOADTEST_TABLE, batch, column_names=column_names)
            elapsed = time.perf_counter() - start

            latencies.append(elapsed)
            print(f"    iteration {i + 1}/{ITERATIONS_PER_BATCH}: {elapsed:.3f}s")

        latencies_ms = [t * 1000 for t in latencies]
        p50 = float(np.percentile(latencies_ms, 50))
        p95 = float(np.percentile(latencies_ms, 95))
        p99 = float(np.percentile(latencies_ms, 99))
        avg_latency = float(np.mean(latencies))
        rows_per_sec = float(batch_size / avg_latency)

        results.append({
            "batch_size": batch_size,
            "p50_ms": round(p50, 2),
            "p95_ms": round(p95, 2),
            "p99_ms": round(p99, 2),
            "rows_per_sec": round(rows_per_sec, 1),
        })
        print(f"    -> p50={p50:.1f}ms p95={p95:.1f}ms p99={p99:.1f}ms rows/s={rows_per_sec:.0f}")

    return results


def run_query(client, sql: str) -> float:
    """Execute a query and return elapsed time in seconds."""
    start = time.perf_counter()
    client.query(sql)
    return time.perf_counter() - start


def benchmark_queries(client) -> dict[str, dict]:
    """Benchmark retention_curve and changepoints queries concurrently."""
    retention_sql_template = (SQL_DIR / "retention_curve.sql").read_text()
    changepoints_sql_template = (SQL_DIR / "changepoints.sql").read_text()

    query_results = {}

    for name, template in [
        ("retention_curve", retention_sql_template),
        ("changepoints", changepoints_sql_template),
    ]:
        print(
            f"  Benchmarking query: {name}"
            f" ({len(TRAILERS)} trailers x {QUERY_ITERATIONS} iterations)"
        )
        latencies = []

        # Build list of (trailer_id, formatted_sql) tasks
        tasks = []
        for trailer_id in TRAILERS:
            sql = template.replace("{trailer_id}", trailer_id)
            for _ in range(QUERY_ITERATIONS):
                tasks.append(sql)

        # Execute concurrently
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(run_query, client, sql) for sql in tasks]
            for future in as_completed(futures):
                latencies.append(future.result())

        latencies_ms = [t * 1000 for t in latencies]
        p50 = float(np.percentile(latencies_ms, 50))
        p95 = float(np.percentile(latencies_ms, 95))
        p99 = float(np.percentile(latencies_ms, 99))

        query_results[name] = {
            "p50_ms": round(p50, 2),
            "p95_ms": round(p95, 2),
            "p99_ms": round(p99, 2),
        }
        print(f"    -> p50={p50:.1f}ms p95={p95:.1f}ms p99={p99:.1f}ms")

    return query_results


def main() -> int:
    print("=" * 60)
    print("CutPoint Load Test: ClickHouse Ingest")
    print("=" * 60)

    client = get_client()

    # Insert benchmarks using a dedicated load-test table
    print("\n[1/2] Insert throughput benchmarks")
    create_loadtest_table(client)
    try:
        insert_results = benchmark_inserts(client)
    finally:
        cleanup_loadtest_table(client)

    # Query benchmarks (uses real data in mv_second_viewers)
    print("\n[2/2] Query latency benchmarks")
    query_results = benchmark_queries(client)

    # Write results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output = {
        "insert_benchmarks": insert_results,
        "query_benchmarks": query_results,
    }
    output_path = RESULTS_DIR / "ingest_results.json"
    output_path.write_text(json.dumps(output, indent=2))
    print(f"\nResults written to {output_path}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
